const state = {
  draftId: null,
  sessionId: null,
  functionCallId: null,
  html: "",
  subject: "",
  preheader: "",
  busy: false,
  sendBusy: false,
  gmailConnected: false,
  gmailConfigured: false,
  gmailEmail: null,
};

const $ = (id) => document.getElementById(id);
const chatHistory = $("chatHistory");
const chatInput = $("chatInput");
const chatSubmit = $("chatSubmit");
const workingText = $("workingText");
const draftStatus = $("draftStatus");
const htmlEditor = $("htmlEditor");
const subjectInput = $("subjectInput");
const preheaderInput = $("preheaderInput");
const emailPreview = $("emailPreview");
const emptyPreview = $("emptyPreview");
const saveDraftBtn = $("saveDraftBtn");
const recipientsInput = $("recipientsInput");
const sendBtn = $("sendBtn");

function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 4500);
}

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = text;
  chatHistory.appendChild(el);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

function updateSendButton() {
  sendBtn.disabled =
    state.busy ||
    state.sendBusy ||
    !state.draftId ||
    !state.gmailConnected ||
    !recipientsInput.value.trim();
}

function setBusy(busy, label = "") {
  state.busy = busy;
  chatInput.disabled = busy;
  chatSubmit.disabled = busy;
  saveDraftBtn.disabled = busy || !state.draftId;
  workingText.textContent = busy ? label : "";

  if (busy) {
    draftStatus.textContent = label || "Agent working…";
    draftStatus.classList.add("working");
  } else {
    draftStatus.textContent = state.draftId ? "Draft ready" : "No draft";
    draftStatus.classList.remove("working");
  }

  updateSendButton();
}

function setSendBusy(busy, label = "") {
  state.sendBusy = busy;
  $("sendWorkingText").textContent = busy ? label : "";
  $("validateBtn").disabled = busy;
  $("disconnectGmailBtn").disabled = busy;
  updateSendButton();
}

function previewHtml(html) {
  if (!html) {
    emailPreview.hidden = true;
    emptyPreview.hidden = false;
    return;
  }

  emptyPreview.hidden = true;
  emailPreview.hidden = false;

  const base = `<base href="${window.location.origin}/">`;

  emailPreview.srcdoc = html.includes("<head")
    ? html.replace(/<head([^>]*)>/i, `<head$1>${base}`)
    : `${base}${html}`;
}

function loadDraft(draft) {
  state.draftId = draft.draft_id || state.draftId;

  if (Object.prototype.hasOwnProperty.call(draft, "session_id")) {
    state.sessionId = draft.session_id;
  }

  if (Object.prototype.hasOwnProperty.call(draft, "function_call_id")) {
    state.functionCallId = draft.function_call_id;
  }

  state.html = draft.html || "";
  state.subject = draft.subject || "";
  state.preheader = draft.preheader || "";

  htmlEditor.value = state.html;
  subjectInput.value = state.subject;
  preheaderInput.value = state.preheader;

  [htmlEditor, subjectInput, preheaderInput].forEach(
    (el) => (el.disabled = false)
  );

  saveDraftBtn.disabled = false;
  draftStatus.textContent = "Draft ready";
  chatSubmit.textContent = "Revise Email";

  previewHtml(state.html);
  updateSendButton();
}

async function api(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }

  return data;
}

async function getApi(path) {
  const response = await fetch(path);
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }

  return data;
}

async function saveDraft(showToast = true) {
  if (!state.draftId) {
    return;
  }

  state.html = htmlEditor.value;
  state.subject = subjectInput.value;
  state.preheader = preheaderInput.value;

  previewHtml(state.html);

  const saved = await api("/api/draft/save", {
    draft_id: state.draftId,
    html: state.html,
    subject: state.subject,
    preheader: state.preheader,
  });

  // The application can restore required elements such as
  // the unsubscribe footer after a manual HTML edit.
  state.html = saved.html || state.html;
  state.subject = saved.subject ?? state.subject;
  state.preheader = saved.preheader ?? state.preheader;

  htmlEditor.value = state.html;
  subjectInput.value = state.subject;
  preheaderInput.value = state.preheader;

  previewHtml(state.html);

  if (showToast) {
    toast("Draft saved.");
  }
}

async function refreshGmailStatus() {
  try {
    const status = await getApi("/api/gmail/status");

    state.gmailConfigured = Boolean(status.configured);
    state.gmailConnected = Boolean(status.connected);
    state.gmailEmail = status.email || null;

    $("connectGmailBtn").hidden =
      state.gmailConnected || !state.gmailConfigured;

    $("disconnectGmailBtn").hidden = !state.gmailConnected;

    if (!state.gmailConfigured) {
      $("gmailStatus").textContent = "OAuth credentials not configured";
      $("oauthNote").textContent =
        "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET, then restart the app.";
    } else if (state.gmailConnected) {
      $("gmailStatus").textContent =
        `Connected: ${state.gmailEmail || "Gmail"}`;

      $("oauthNote").textContent =
        "Approve & Send will send one personalized message to each sendable recipient.";
    } else {
      $("gmailStatus").textContent = "No Gmail account connected";
      $("oauthNote").textContent =
        "Connect the Gmail account that should send Serenity Blooms email.";
    }
  } catch (error) {
    state.gmailConnected = false;

    $("gmailStatus").textContent =
      "Could not check Gmail connection";

    $("oauthNote").textContent = error.message;
  }

  updateSendButton();
}

$("chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  const message = chatInput.value.trim();

  if (!message || state.busy) {
    return;
  }

  addMessage("user", message);
  chatInput.value = "";

  try {
    if (!state.draftId) {
      setBusy(true, "Creating your email…");

      const draft = await api("/api/generate", {
        prompt: message,
      });

      loadDraft(draft);

      addMessage(
        "assistant",
        "I created the first draft. You can preview it, edit the HTML directly, or tell me what you would like changed."
      );
    } else {
      await saveDraft(false);

      setBusy(true, "Revising your email…");

      if (!state.sessionId || !state.functionCallId) {
        throw new Error(
          "The review session is no longer available. Start a new email."
        );
      }

      const draft = await api("/api/revise", {
        draft_id: state.draftId,
        session_id: state.sessionId,
        function_call_id: state.functionCallId,
        feedback: message,
        html: state.html,
        subject: state.subject,
        preheader: state.preheader,
      });

      loadDraft(draft);

      if (draft.approved) {
        addMessage("assistant", "The email is approved.");
      } else {
        addMessage(
          "assistant",
          "I applied that revision to the current draft."
        );
      }
    }
  } catch (error) {
    addMessage(
      "assistant",
      `I couldn't complete that request: ${error.message}`
    );

    toast(error.message, true);
  } finally {
    setBusy(false);
  }
});

saveDraftBtn.addEventListener("click", async () => {
  try {
    await saveDraft(true);
  } catch (error) {
    toast(error.message, true);
  }
});

htmlEditor.addEventListener("input", () => {
  previewHtml(htmlEditor.value);
});

subjectInput.addEventListener("input", () => {
  state.subject = subjectInput.value;
});

preheaderInput.addEventListener("input", () => {
  state.preheader = preheaderInput.value;
});

recipientsInput.addEventListener("input", () => {
  $("recipientSummary").textContent =
    "Recipient list changed. Check recipients again before sending.";

  updateSendButton();
});

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    document
      .querySelectorAll(".tab")
      .forEach((t) => t.classList.remove("active"));

    document
      .querySelectorAll(".tab-pane")
      .forEach((p) => p.classList.remove("active"));

    tab.classList.add("active");

    $(`${tab.dataset.tab}Pane`).classList.add("active");
  });
}

$("newEmailBtn").addEventListener("click", () => {
  state.draftId = null;
  state.sessionId = null;
  state.functionCallId = null;

  state.html = "";
  state.subject = "";
  state.preheader = "";

  htmlEditor.value = "";
  subjectInput.value = "";
  preheaderInput.value = "";

  [htmlEditor, subjectInput, preheaderInput].forEach(
    (el) => (el.disabled = true)
  );

  saveDraftBtn.disabled = true;

  draftStatus.textContent = "No draft";
  chatSubmit.textContent = "Generate Email";

  chatHistory.innerHTML = "";

  addMessage(
    "assistant",
    "Describe the next marketing email you want to create."
  );

  previewHtml("");
  updateSendButton();

  chatInput.focus();
});

$("validateBtn").addEventListener("click", async () => {
  try {
    const result = await api(
      "/api/recipients/validate",
      {
        recipients: recipientsInput.value,
      }
    );

    const c = result.counts;

    $("recipientSummary").textContent =
      `${c.sendable} sendable · ` +
      `${c.suppressed} unsubscribed · ` +
      `${c.invalid} invalid`;

    if (c.invalid || c.suppressed) {
      const details = [];

      if (c.suppressed) {
        details.push(
          `Suppressed: ${result.suppressed.join(", ")}`
        );
      }

      if (c.invalid) {
        details.push(
          `Invalid: ${result.invalid.join(", ")}`
        );
      }

      toast(details.join(" | "));
    } else {
      toast("Recipient list looks good.");
    }
  } catch (error) {
    toast(error.message, true);
  }
});

$("disconnectGmailBtn").addEventListener(
  "click",
  async () => {
    try {
      await api("/api/gmail/disconnect");

      toast("Gmail disconnected.");

      await refreshGmailStatus();
    } catch (error) {
      toast(error.message, true);
    }
  }
);

sendBtn.addEventListener("click", async () => {
  if (state.sendBusy || !state.draftId) {
    return;
  }

  try {
    await saveDraft(false);

    // The button is "Approve & Send", so first resolve
    // the ADK request_input pause with an approval response.
    //
    // This allows the LoopAgent to exit normally before
    // the application sends the email through Gmail.
    if (state.sessionId && state.functionCallId) {
      setSendBusy(true, "Approving email…");

      const approvedDraft = await api(
        "/api/revise",
        {
          draft_id: state.draftId,
          session_id: state.sessionId,
          function_call_id: state.functionCallId,
          feedback: "approve",
          html: state.html,
          subject: state.subject,
          preheader: state.preheader,
        }
      );

      loadDraft(approvedDraft);

      if (!approvedDraft.approved) {
        throw new Error(
          "The agent workflow did not complete approval."
        );
      }
    }

    const validation = await api(
      "/api/recipients/validate",
      {
        recipients: recipientsInput.value,
      }
    );

    const c = validation.counts;

    $("recipientSummary").textContent =
      `${c.sendable} sendable · ` +
      `${c.suppressed} unsubscribed · ` +
      `${c.invalid} invalid`;

    if (!c.sendable) {
      throw new Error(
        "There are no sendable recipients."
      );
    }

    const ignored =
      c.suppressed + c.invalid;

    const message = ignored
      ? `Send this email to ${c.sendable} recipient(s)? ${ignored} invalid or unsubscribed address(es) will not be sent.`
      : `Send this email to ${c.sendable} recipient(s) from ${state.gmailEmail || "the connected Gmail account"}?`;

    if (!window.confirm(message)) {
      return;
    }

    setSendBusy(
      true,
      "Sending approved email…"
    );

    const result = await api(
      "/api/send",
      {
        draft_id: state.draftId,
        recipients: recipientsInput.value,
      }
    );

    const counts = result.counts;

    $("recipientSummary").textContent =
      `${counts.sent} sent · ` +
      `${counts.suppressed + counts.suppressed_before_send} unsubscribed · ` +
      `${counts.invalid} invalid · ` +
      `${counts.failed} failed`;

    if (counts.failed) {
      toast(
        `Sent ${counts.sent}; ${counts.failed} message(s) failed.`,
        true
      );
    } else {
      toast(
        `Email sent successfully to ${counts.sent} recipient(s).`
      );
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    setSendBusy(false);
  }
});

function handleOAuthReturn() {
  const params =
    new URLSearchParams(window.location.search);

  if (params.get("gmail") === "connected") {
    toast("Gmail connected successfully.");

    window.history.replaceState(
      {},
      "",
      window.location.pathname
    );
  } else if (params.get("gmail_error")) {
    toast(
      `Gmail connection failed: ${params.get("gmail_error")}`,
      true
    );

    window.history.replaceState(
      {},
      "",
      window.location.pathname
    );
  }
}

handleOAuthReturn();
refreshGmailStatus();
updateSendButton();