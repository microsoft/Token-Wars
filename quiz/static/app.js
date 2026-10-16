"use strict";

const $ = id => document.getElementById(id);
const mobile = location.pathname === "/join";
const escapeHtml = value => String(value).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const percent = value => value === null ? "--" : `${(value * 100).toFixed(1)}%`;
let state;
let participantId;
let renderedEvent;
let refreshing = false;
let submitting = false;
let coachAction;
let coachEvent;
let coachBusy = false;

function newParticipantId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  // randomUUID requires HTTPS; getRandomValues also supports trusted LAN demos.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}

function takeaways(data, personal = false) {
  return data.questions.map((question, index) => {
    const result = data.question_results.find(row => row.id === question.id);
    const chosen = data.receipt?.answers?.[question.id];
    const feedback = personal && chosen !== undefined
      ? `<p>Your answer: ${escapeHtml(question.options[chosen])} -
          ${chosen === question.correct_option ? "Correct!" : "A takeaway to carry forward."}</p>` : "";
    return `<article class="takeaway">
      <div class="eyebrow">${escapeHtml(question.challenge)}</div>
      <h3>${index + 1}. ${escapeHtml(question.prompt)}</h3>
      <p class="answer">Correct answer: ${escapeHtml(question.options[question.correct_option])}</p>
      ${feedback}
      <p class="achievement">${escapeHtml(question.takeaway)}</p>
      <p class="muted small">${result.correct_count} of ${result.participants} participants answered correctly.</p>
    </article>`;
  }).join("");
}

function renderBoard(data) {
  $("participants").textContent = data.participants;
  $("correct-total").textContent = data.correct_count;
  $("question-count").textContent = data.questions.length;
  $("phase").textContent = { open: "LIVE / OPEN", closed: "RESPONSES CLOSED", revealed: "TAKEAWAYS REVEALED" }[data.phase];
  $("join-heading").textContent = data.phase === "open" ? "SCAN. JOIN. REINFORCE." : "SCAN TO VIEW THE DEBRIEF";
  $("join-link").href = data.join_url;
  $("join-url").textContent = data.join_url;
  $("rankings").innerHTML = data.leaderboard.map(row => `<tr class="${row.rank === 1 ? "winner" : ""}">
    <td>${row.rank ?? "--"}</td><td>${escapeHtml(row.team)}</td><td>${percent(row.accuracy)}</td>
    <td>${row.correct_count} / ${row.answer_count}</td><td>${row.participants}</td></tr>`).join("");
  $("reveal-message").textContent = data.phase === "revealed"
    ? `${data.participants} participants. ${data.correct_count} correct answers. Here is what you achieved together.`
    : "Answers stay hidden until the coach closes the quiz and reveals the takeaways.";
  $("board-takeaways").innerHTML = data.phase === "revealed" ? takeaways(data) : "";
  document.querySelectorAll("[data-action]").forEach(button => {
    const action = button.dataset.action;
    button.disabled = !data.admin_enabled || coachBusy
      || (action === "close" && data.phase !== "open")
      || (action === "reveal" && data.phase !== "closed");
  });
  if (!data.admin_enabled) $("coach-status").textContent = "Coach controls are disabled. Configure QUIZ_ADMIN_KEY.";
}

function renderQuestions(data) {
  $("team").innerHTML = '<option value="">Choose your team</option>'
    + data.teams.map(team => `<option value="${escapeHtml(team)}">${escapeHtml(team)}</option>`).join("");
  $("questions").innerHTML = data.questions.map((question, index) => `<fieldset>
    <legend><small>${index + 1} / ${data.questions.length} - ${escapeHtml(question.challenge)}</small>${escapeHtml(question.prompt)}</legend>
    ${question.options.map((option, choice) => `<label class="option">
      <input type="radio" name="question-${index}" value="${choice}" required>
      <span>${escapeHtml(option)}</span></label>`).join("")}
    </fieldset>`).join("");
  $("submit-status").textContent = "";
  updateProgress();
}

function renderMobile(data) {
  if (renderedEvent !== data.event_id) {
    const saved = localStorage.getItem("tokenwars-quiz-participant");
    let identity;
    try {
      identity = saved ? JSON.parse(saved) : null;
    } catch {
      throw new Error("Saved quiz identity is invalid. Clear this site's storage and reload.");
    }
    if (identity?.event_id !== data.event_id || !identity?.participant_id) {
      identity = { event_id: data.event_id, participant_id: newParticipantId() };
      localStorage.setItem("tokenwars-quiz-participant", JSON.stringify(identity));
    }
    participantId = identity.participant_id;
    renderedEvent = data.event_id;
    renderQuestions(data);
    // Fetch the saved receipt before allowing another submission after a reload.
    return false;
  }
  const receipt = data.receipt;
  $("quiz-form").hidden = Boolean(receipt) || data.phase !== "open";
  $("mobile-title").textContent = receipt ? "Response locked in. Thank you!"
    : data.phase === "open" ? "Ready to put it all together?" : "Responses are closed";
  $("mobile-description").textContent = receipt
    ? `Your response for ${receipt.team} is recorded. ${data.phase === "revealed"
      ? `You answered ${receipt.correct_count} of ${data.questions.length} correctly.`
      : "Watch for the coach to reveal the answers and takeaways."}`
    : data.phase === "open" ? "Choose one answer per question. You can submit once; answers will be revealed by the coach."
      : "You can still explore the takeaways when the coach reveals them.";
  $("mobile-debrief").hidden = data.phase !== "revealed";
  $("mobile-takeaways").innerHTML = data.phase === "revealed" ? takeaways(data, true) : "";
  $("submit").disabled = submitting;
  return true;
}

function updateProgress() {
  if (!state) return;
  const answered = $("questions").querySelectorAll("input:checked").length;
  $("progress").textContent = `${answered} of ${state.questions.length} answered`;
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    let data = await api(`/api/state${mobile && participantId ? `?participant_id=${encodeURIComponent(participantId)}` : ""}`);
    state = data;
    document.title = data.title;
    if (mobile && !renderMobile(data)) {
      data = await api(`/api/state?participant_id=${encodeURIComponent(participantId)}`);
      state = data;
      if (!renderMobile(data)) throw new Error("The event changed while loading. Please reload.");
    }
    if (!mobile) renderBoard(data);
    $("participant").hidden = !mobile;
    $("dashboard").hidden = mobile;
    $("connection").classList.remove("error");
    $("connection").textContent = data.phase === "open"
      ? `Arena is open - ${data.questions.length} questions. Make every takeaway count.`
      : data.phase === "closed" ? "Responses are closed. The debrief is coming up." : "The takeaways are live. See what you achieved.";
    $("updated").textContent = `Live - updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    $("connection").classList.add("error");
    $("connection").textContent = `Unable to refresh: ${error.message} Retrying in 5 seconds.`;
  } finally {
    refreshing = false;
  }
}

$("quiz-form").addEventListener("change", updateProgress);
$("quiz-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting || !state || !participantId) return;
  const answers = {};
  state.questions.forEach((question, index) => {
    answers[question.id] = Number(new FormData($("quiz-form")).get(`question-${index}`));
  });
  submitting = true;
  $("submit").disabled = true;
  $("submit-status").textContent = "Recording your answers...";
  try {
    await api("/api/responses", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_id: state.event_id, participant_id: participantId, team: $("team").value, answers })
    });
    $("quiz-form").hidden = true;
    $("submit-status").textContent = "Your response is recorded. Thank you!";
    await refresh();
  } catch (error) {
    $("submit-status").textContent = `Not confirmed: ${error.message} You can retry safely; the same response will not count twice.`;
  } finally {
    submitting = false;
    $("submit").disabled = false;
  }
});

const descriptions = {
  close: "Stop accepting new responses. You can reveal the answers after this step.",
  reveal: "Make all correct answers and reinforcement messages public on the dashboard and every phone.",
  export: "Download all responses, scores and the answer key. Keep this file coach-only.",
  reset: "Permanently delete all responses and start a new event. Export first; this cannot be undone."
};
document.querySelectorAll("[data-action]").forEach(button => {
  button.addEventListener("click", () => {
    coachAction = button.dataset.action;
    coachEvent = state.event_id;
    $("coach-title").textContent = button.textContent;
    $("coach-description").textContent = descriptions[coachAction];
    $("coach-error").textContent = "";
    $("coach-key").value = "";
    $("reset-confirmation").value = "";
    $("reset-confirmation").hidden = coachAction !== "reset";
    $("reset-label").hidden = coachAction !== "reset";
    $("reset-confirmation").required = coachAction === "reset";
    $("coach-dialog").showModal();
    $("coach-key").focus();
  });
});
$("cancel-coach").addEventListener("click", () => $("coach-dialog").close());
$("coach-dialog").addEventListener("close", () => { $("coach-key").value = ""; });
$("coach-dialog").addEventListener("cancel", event => { if (coachBusy) event.preventDefault(); });
$("coach-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (coachBusy) return;
  if (coachAction === "reset" && $("reset-confirmation").value !== "RESET") {
    $("coach-error").textContent = "Type RESET to confirm.";
    return;
  }
  coachBusy = true;
  $("confirm-coach").disabled = true;
  $("cancel-coach").disabled = true;
  $("coach-error").textContent = "";
  const key = $("coach-key").value;
  $("coach-key").value = "";
  try {
    if (coachAction === "export") {
      const data = await api("/api/admin/export", { headers: { "x-admin-key": key } });
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `tokenwars-quiz-${data.event_id}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } else {
      await api(`/api/admin/${coachAction}`, {
        method: "POST", headers: { "Content-Type": "application/json", "x-admin-key": key },
        body: JSON.stringify({ event_id: coachEvent })
      });
    }
    $("coach-status").textContent = { close: "Responses closed.", reveal: "Answers and achievements revealed.",
      reset: "New event started. All previous responses cleared.", export: "Results downloaded. Keep the answer key private." }[coachAction];
    $("coach-dialog").close();
    await refresh();
  } catch (error) {
    $("coach-error").textContent = error.message;
  } finally {
    coachBusy = false;
    $("confirm-coach").disabled = false;
    $("cancel-coach").disabled = false;
    if (state && !mobile) renderBoard(state);
  }
});

refresh();
setInterval(refresh, 5000);
