import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceModifiedError
from azure.storage.blob import BlobServiceClient

import server
from storage import BlobStore, BoardNotFound, StorageConflict, container_name


class BlobFailureTests(unittest.TestCase):
    def test_configured_blob_failure_never_falls_back_to_local_disk(self):
        service = Mock()
        service.create_container.side_effect = AzureError("storage unavailable")
        local = Mock()
        with patch.multiple(server, store=local, STORAGE_URL="https://test.blob.core.windows.net",
                            SUBMIT_KEY="event", ADMIN_KEY="coach"), \
                patch.object(server, "ManagedIdentityCredential"), \
                patch.object(server, "BlobServiceClient", return_value=service):
            with self.assertRaises(AzureError):
                server.initialize_storage()
            local.create.assert_not_called()

    def test_conditional_conflict_reloads_instead_of_losing_accepted_runs(self):
        blob = Mock()
        blob.upload_blob.side_effect = [ResourceModifiedError("etag changed"), None]
        store = BlobStore(Mock())
        with patch.object(store, "_read", side_effect=[
                (blob, [{"id": "first"}], '"v1"'),
                (blob, [{"id": "first"}, {"id": "concurrent"}], '"v2"')]):
            records = store.append("key", {"id": "new"})
        self.assertEqual([record["id"] for record in records], ["first", "concurrent", "new"])
        self.assertEqual(blob.upload_blob.call_args.kwargs["etag"], '"v2"')

    def test_conflicts_exhaust_explicitly_without_unconditional_overwrite(self):
        for error in (ResourceModifiedError("etag changed"), ResourceExistsError("first write raced")):
            blob = Mock()
            blob.upload_blob.side_effect = error
            store = BlobStore(Mock())
            with patch.object(store, "_read", return_value=(blob, [], None)):
                with self.assertRaises(StorageConflict):
                    store.append("key", {"id": "new"})
            self.assertTrue(all(call.kwargs["overwrite"] is False
                                for call in blob.upload_blob.call_args_list))

    def test_open_state_lives_in_container_metadata_and_defaults_to_open(self):
        container = Mock()
        container.get_container_properties.return_value = Mock(metadata={"leaderboard_key": "ZXZlbnQ="})
        service = Mock()
        service.get_container_client.return_value = container
        store = BlobStore(service)
        self.assertTrue(store.is_open("event"))
        store.set_open("event", False)
        container.set_container_metadata.assert_called_once_with(
            {"leaderboard_key": "ZXZlbnQ=", "submissions_open": "false"})
        container.get_container_properties.return_value = Mock(
            metadata={"leaderboard_key": "ZXZlbnQ=", "submissions_open": "false"})
        self.assertFalse(store.is_open("event"))


@unittest.skipUnless(os.environ.get("TOKENWARS_TEST_BLOB_CONNECTION_STRING"),
                     "Set TOKENWARS_TEST_BLOB_CONNECTION_STRING to an isolated Azurite endpoint")
class BlobPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.connection = os.environ["TOKENWARS_TEST_BLOB_CONNECTION_STRING"]
        self.service = BlobServiceClient.from_connection_string(self.connection)
        self.addCleanup(self.service.close)
        self.store = BlobStore(self.service)
        self.key = "test/" + uuid.uuid4().hex
        self.other = "test/" + uuid.uuid4().hex
        for key in (self.key, self.other):
            self.assertTrue(self.store.create(key))
            self.addCleanup(self.service.delete_container, container_name(key))

    def test_empty_creation_duplicate_and_restart_persist_both_boards(self):
        self.assertEqual(self.store.load(self.key), [])
        self.store.append(self.key, {"id": "one", "strategy": {"concurrency": 2}})
        self.store.append(self.other, {"id": "two"})
        service = BlobServiceClient.from_connection_string(self.connection)
        self.addCleanup(service.close)
        restarted = BlobStore(service)
        self.assertFalse(restarted.create(self.key))
        self.assertEqual(restarted.load(self.key), [{"id": "one", "strategy": {"concurrency": 2}}])
        self.assertEqual(restarted.load(self.other), [{"id": "two"}])
        self.assertIn(self.key, restarted.list_keys())
        self.assertIn(self.other, restarted.list_keys())

    def test_scoped_reset_survives_restart_and_keeps_empty_container(self):
        self.store.set_open(self.key, False)
        self.store.append(self.key, {"id": "one"})
        self.store.append(self.other, {"id": "two"})
        self.store.reset(self.key)
        restarted = BlobStore(self.service)
        self.assertFalse(restarted.is_open(self.key))
        self.assertTrue(restarted.is_open(self.other))
        self.assertIn(self.key, restarted.list_keys())
        self.assertEqual(restarted.load(self.key), [])
        self.assertEqual(restarted.load(self.other), [{"id": "two"}])
        self.assertFalse(restarted.create(self.key))
        restarted.append(self.key, {"id": "new"})
        self.assertEqual(restarted.load(self.key), [{"id": "new"}])

    def test_multiple_clients_append_without_lost_records(self):
        def append(index):
            with BlobServiceClient.from_connection_string(self.connection) as service:
                return BlobStore(service).append(self.key, {"id": str(index)})

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(append, range(12)))
        self.assertEqual(len(results), 12)
        self.assertEqual({row["id"] for row in self.store.load(self.key)},
                         {str(index) for index in range(12)})

    def test_unknown_container_is_not_implicitly_created(self):
        unknown = "test/" + uuid.uuid4().hex
        with self.assertRaises(BoardNotFound):
            self.store.append(unknown, {"id": "rejected"})
        self.assertFalse(self.service.get_container_client(container_name(unknown)).exists())
