const state = {
  draftId: null,
  html: "",
  subject: "",
  preheader: "",
  busy: false,
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

function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 3500);
}

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = text;
  chatHistory.appendChild(el);
  chatHistory.scrollTop = chatHistory.scrollHeight;
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
  state.html = draft.html || "";
  state.subject = draft.subject || "";
  state.preheader = draft.preheader || "";
  htmlEditor.value = state.html;
  subjectInput.value = state.subject;
  preheaderInput.value = state.preheader;
  [htmlEditor, subjectInput, preheaderInput].forEach((el) => (el.disabled = false));
  saveDraftBtn.disabled = false;
  draftStatus.textContent = "Draft ready";
  chatSubmit.textContent = "Revise Email";
  previewHtml(state.html);
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function saveDraft(showToast = true) {
  if (!state.draftId) return;
  state.html = htmlEditor.value;
  state.subject = subjectInput.value;
  state.preheader = preheaderInput.value;
  previewHtml(state.html);
  await api("/api/draft/save", {
    draft_id: state.draftId,
    html: state.html,
    subject: state.subject,
    preheader: state.preheader,
  });
  if (showToast) toast("Draft saved.");
}

$("chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message || state.busy) return;
  addMessage("user", message);
  chatInput.value = "";

  try {
    if (!state.draftId) {
      setBusy(true, "Creating your email…");
      const draft = await api("/api/generate", { prompt: message });
      loadDraft(draft);
      addMessage("assistant", "I created the first draft. You can preview it, edit the HTML directly, or tell me what you would like changed.");
    } else {
      await saveDraft(false);
      setBusy(true, "Revising your email…");
      const draft = await api("/api/revise", {
        draft_id: state.draftId,
        feedback: message,
        html: state.html,
        subject: state.subject,
        preheader: state.preheader,
      });
      loadDraft(draft);
      addMessage("assistant", "I applied that revision to the current draft.");
    }
  } catch (error) {
    addMessage("assistant", `I couldn't complete that request: ${error.message}`);
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
});

saveDraftBtn.addEventListener("click", async () => {
  try { await saveDraft(true); }
  catch (error) { toast(error.message, true); }
});

htmlEditor.addEventListener("input", () => previewHtml(htmlEditor.value));

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`${tab.dataset.tab}Pane`).classList.add("active");
  });
}

$("newEmailBtn").addEventListener("click", () => {
  state.draftId = null;
  state.html = "";
  state.subject = "";
  state.preheader = "";
  htmlEditor.value = "";
  subjectInput.value = "";
  preheaderInput.value = "";
  [htmlEditor, subjectInput, preheaderInput].forEach((el) => (el.disabled = true));
  saveDraftBtn.disabled = true;
  draftStatus.textContent = "No draft";
  chatSubmit.textContent = "Generate Email";
  chatHistory.innerHTML = "";
  addMessage("assistant", "Describe the next marketing email you want to create.");
  previewHtml("");
  chatInput.focus();
});

$("validateBtn").addEventListener("click", async () => {
  try {
    const result = await api("/api/recipients/validate", { recipients: $("recipientsInput").value });
    const c = result.counts;
    $("recipientSummary").textContent = `${c.sendable} sendable · ${c.suppressed} unsubscribed · ${c.invalid} invalid`;
    if (c.invalid || c.suppressed) {
      const details = [];
      if (c.suppressed) details.push(`Suppressed: ${result.suppressed.join(", ")}`);
      if (c.invalid) details.push(`Invalid: ${result.invalid.join(", ")}`);
      toast(details.join(" | "));
    } else {
      toast("Recipient list looks good.");
    }
  } catch (error) { toast(error.message, true); }
});
