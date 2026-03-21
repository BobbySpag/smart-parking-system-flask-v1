const byId = (id) => document.getElementById(id);

const apiStatus = byId("apiStatus");
const logBox = byId("logBox");
const tokenBox = byId("tokenBox");
const slotsContainer = byId("slotsContainer");

const state = {
  token: localStorage.getItem("parking_token") || "",
};

if (state.token) tokenBox.value = state.token;

function log(message, payload) {
  const line = `[${new Date().toLocaleTimeString()}] ${message}`;
  const next = payload ? `${line}\n${JSON.stringify(payload, null, 2)}\n` : `${line}\n`;
  logBox.textContent = next + logBox.textContent;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;

  const res = await fetch(path, { ...options, headers });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = { raw: await res.text() };
  }
  if (!res.ok) throw { status: res.status, data };
  return data;
}

function renderSlots(slots) {
  if (!Array.isArray(slots) || slots.length === 0) {
    slotsContainer.innerHTML = "<p>No slots found.</p>";
    return;
  }

  const rows = slots
    .map((s) => `<tr><td>${s.id}</td><td>${s.location}</td><td>${s.status}</td></tr>`)
    .join("");

  slotsContainer.innerHTML = `
    <table>
      <thead><tr><th>ID</th><th>Location</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function refreshSlots() {
  try {
    const slots = await api("/slots", { method: "GET", headers: {} });
    renderSlots(slots);
    apiStatus.textContent = "API: connected";
  } catch (err) {
    apiStatus.textContent = "API: error";
    log("Failed to load slots", err);
  }
}

byId("registerBtn").addEventListener("click", async () => {
  const username = byId("registerUsername").value.trim();
  const password = byId("registerPassword").value;
  try {
    const data = await api("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    log("Register success", data);
  } catch (err) {
    log("Register failed", err);
  }
});

byId("loginBtn").addEventListener("click", async () => {
  const username = byId("loginUsername").value.trim();
  const password = byId("loginPassword").value;
  try {
    const data = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    state.token = data.access_token;
    tokenBox.value = state.token;
    localStorage.setItem("parking_token", state.token);
    log("Login success", { token_type: data.token_type });
  } catch (err) {
    log("Login failed", err);
  }
});

byId("saveTokenBtn").addEventListener("click", () => {
  state.token = tokenBox.value.trim();
  localStorage.setItem("parking_token", state.token);
  log("Token saved manually");
});

byId("clearTokenBtn").addEventListener("click", () => {
  state.token = "";
  tokenBox.value = "";
  localStorage.removeItem("parking_token");
  log("Token cleared");
});

byId("addSlotBtn").addEventListener("click", async () => {
  const location = byId("addLocation").value.trim();
  try {
    const data = await api("/add-slot", {
      method: "POST",
      body: JSON.stringify({ location }),
    });
    log("Slot added", data);
    refreshSlots();
  } catch (err) {
    log("Add slot failed", err);
  }
});

byId("bookBtn").addEventListener("click", async () => {
  const id = Number(byId("slotIdInput").value);
  try {
    const data = await api("/book", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    log("Book success", data);
    refreshSlots();
  } catch (err) {
    log("Book failed", err);
  }
});

byId("releaseBtn").addEventListener("click", async () => {
  const id = Number(byId("slotIdInput").value);
  try {
    const data = await api("/release-slot", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    log("Release success", data);
    refreshSlots();
  } catch (err) {
    log("Release failed", err);
  }
});

byId("refreshBtn").addEventListener("click", refreshSlots);

refreshSlots();
