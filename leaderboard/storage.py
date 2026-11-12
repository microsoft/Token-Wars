"""Persistent, key-scoped leaderboard storage."""
import base64
import hashlib
import json
import os
import threading

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.storage.blob import ContentSettings

PREFIX = "twlb-"


def container_name(key):
    return PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()[:56]


class BoardNotFound(Exception):
    pass


class StorageConflict(Exception):
    pass


class LocalStore:
    """Local development only; one process, atomic file replacement."""

    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.RLock()

    def _path(self, key):
        return os.path.join(self.directory, container_name(key) + ".json")

    def _read(self, key):
        try:
            with open(self._path(key), encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError as exc:
            raise BoardNotFound() from exc

    def _write(self, key, records):
        os.makedirs(self.directory, exist_ok=True)
        path = self._path(key)
        with open(path + ".tmp", "w", encoding="utf-8") as file:
            json.dump({"key": key, "submissions": records}, file, allow_nan=False)
        os.replace(path + ".tmp", path)

    def create(self, key):
        with self.lock:
            if os.path.exists(self._path(key)):
                return False
            self._write(key, [])
            return True

    def list_keys(self):
        with self.lock:
            if not os.path.exists(self.directory):
                return []
            keys = []
            for name in os.listdir(self.directory):
                if name.startswith(PREFIX) and name.endswith(".json"):
                    with open(os.path.join(self.directory, name), encoding="utf-8") as file:
                        keys.append(json.load(file)["key"])
            return sorted(keys)

    def load(self, key):
        with self.lock:
            return self._read(key)["submissions"]

    def append(self, key, record):
        with self.lock:
            records = self.load(key)
            records.append(record)
            self._write(key, records)
            return records

    def reset(self, key):
        with self.lock:
            self._read(key)
            self._write(key, [])


class BlobStore:
    """One private container per key; conditional writes work across replicas."""

    def __init__(self, service):
        self.service = service

    def create(self, key):
        encoded = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii")
        try:
            self.service.create_container(container_name(key), metadata={"leaderboard_key": encoded})
            return True
        except ResourceExistsError:
            return False

    def list_keys(self):
        keys = []
        for container in self.service.list_containers(name_starts_with=PREFIX, include_metadata=True):
            key = base64.urlsafe_b64decode(container.metadata["leaderboard_key"]).decode("utf-8")
            if container.name != container_name(key):
                raise ValueError("Leaderboard container metadata does not match its name")
            keys.append(key)
        return sorted(keys)

    def _read(self, key):
        container = self.service.get_container_client(container_name(key))
        try:
            container.get_container_properties()
        except ResourceNotFoundError as exc:
            raise BoardNotFound() from exc
        blob = container.get_blob_client("submissions.json")
        try:
            download = blob.download_blob()
            return blob, json.loads(download.readall()), download.properties.etag
        except ResourceNotFoundError as exc:
            if exc.error_code != "BlobNotFound":
                raise
            return blob, [], None

    def load(self, key):
        return self._read(key)[1]

    def _update(self, key, record=None):
        for _ in range(8):
            blob, records, etag = self._read(key)
            records = records + [record] if record is not None else []
            options = {"overwrite": False}
            if etag is not None:
                options = {"overwrite": True, "etag": etag,
                           "match_condition": MatchConditions.IfNotModified}
            try:
                blob.upload_blob(
                    json.dumps(records, allow_nan=False).encode("utf-8"),
                    content_settings=ContentSettings(content_type="application/json"),
                    **options,
                )
                return records
            except (ResourceModifiedError, ResourceExistsError):
                continue
        raise StorageConflict("Leaderboard is busy; retry the request")

    def append(self, key, record):
        return self._update(key, record)

    def reset(self, key):
        self._update(key)
