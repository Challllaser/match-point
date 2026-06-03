document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => button.closest(".flash")?.remove());
});

document.querySelectorAll("select[name='status']").forEach((select) => {
  const current = select.closest("tr")?.querySelector(".badge")?.textContent?.trim();
  if (current) select.value = current;
});

const disciplineSelect = document.querySelector("[data-discipline-select]");
const mapGroups = document.querySelectorAll(".map-group");
const rulesSelect = document.querySelector("[data-rules-select]");

function updateMapPool() {
  if (!disciplineSelect || !mapGroups.length) return;
  const game = disciplineSelect.selectedOptions[0]?.dataset.game;
  mapGroups.forEach((group) => {
    const active = group.dataset.game === game;
    group.classList.toggle("active", active);
    group.querySelectorAll("input").forEach((input) => {
      input.disabled = !active;
      if (!active) input.checked = false;
    });
  });
  if (rulesSelect) {
    let firstVisibleOption = null;
    rulesSelect.querySelectorAll("optgroup").forEach((group) => {
      const active = group.dataset.game === game;
      group.hidden = !active;
      group.disabled = !active;
      group.querySelectorAll("option").forEach((option) => {
        option.hidden = !active;
        option.disabled = !active;
        if (active && !firstVisibleOption) firstVisibleOption = option;
      });
    });
    if (!rulesSelect.selectedOptions[0] || rulesSelect.selectedOptions[0].disabled) {
      rulesSelect.value = firstVisibleOption?.value || "";
    }
  }
}

disciplineSelect?.addEventListener("change", updateMapPool);
updateMapPool();

let audioContext;
function uiSound(type = "tap") {
  try {
    audioContext ||= new AudioContext();
    const osc = audioContext.createOscillator();
    const gain = audioContext.createGain();
    osc.type = type === "confirm" ? "triangle" : "sine";
    osc.frequency.value = type === "confirm" ? 720 : 420;
    gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.035, audioContext.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.11);
    osc.connect(gain);
    gain.connect(audioContext.destination);
    osc.start();
    osc.stop(audioContext.currentTime + 0.12);
  } catch (_) {}
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("a, button, input[type='checkbox'], select");
  if (target) uiSound(target.matches("button, .primary") ? "confirm" : "tap");
});

document.querySelectorAll("[data-reset-after-submit]").forEach((form) => {
  form.addEventListener("submit", () => {
    setTimeout(() => form.reset(), 50);
  });
});

document.querySelectorAll("[data-chat-feed]").forEach((chat) => {
  const feedUrl = chat.dataset.chatFeed;
  const messages = chat.querySelector("[data-chat-messages]");
  const form = chat.querySelector("[data-chat-form]");
  const input = form?.querySelector("input[name='body']");
  const emojiInput = form?.querySelector("[data-emoji-input]");
  const emojiPanel = form?.querySelector("[data-emoji-panel]");
  const emojiToggle = form?.querySelector("[data-emoji-toggle]");

  async function refreshChat(keepPosition = false) {
    if (!feedUrl || !messages) return;
    const nearBottom = messages.scrollTop + messages.clientHeight >= messages.scrollHeight - 80;
    try {
      const response = await fetch(feedUrl, { cache: "no-store" });
      if (!response.ok) return;
      const html = await response.text();
      if (messages.innerHTML !== html) {
        messages.innerHTML = html;
        if (!keepPosition || nearBottom) messages.scrollTop = messages.scrollHeight;
      }
    } catch (_) {}
  }

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    if (!String(data.get("body") || "").trim() && !String(data.get("emoji") || "").trim()) return;
    try {
      await fetch(form.action, { method: "POST", body: data, headers: { "X-Requested-With": "fetch" } });
      form.reset();
      if (emojiInput) emojiInput.value = "";
      if (emojiPanel) emojiPanel.hidden = true;
      await refreshChat();
    } catch (_) {
      form.submit();
    }
  });

  form?.querySelectorAll("[data-emoji]").forEach((button) => {
    button.addEventListener("click", () => {
      if (emojiInput) emojiInput.value = button.dataset.emoji || "";
      if (emojiPanel) emojiPanel.hidden = true;
      form.requestSubmit();
    });
  });

  emojiToggle?.addEventListener("click", () => {
    if (emojiPanel) emojiPanel.hidden = !emojiPanel.hidden;
  });

  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  refreshChat();
  setInterval(() => refreshChat(true), 3500);
});
