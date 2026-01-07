const TOKEN_KEY = "kazino_token";
const IMAGE_EXTS = ["jpg", "png", "webp", "jpeg"];

const state = {
  token: localStorage.getItem(TOKEN_KEY),
  config: null,
  user: null,
  caseWeapons: {},
  selectedCase: null,
  upgradeChance: 50,
  upgradeSelected: [],
  upgradeTargets: [],
  upgradeTarget: null,
  giveaways: [],
  feed: [],
  notifications: []
};

document.addEventListener("DOMContentLoaded", () => {
  bootstrap();
});

async function bootstrap() {
  await loadPartials();
  await init();
}

async function loadPartials() {
  const slots = Array.from(document.querySelectorAll("[data-include]"));
  await Promise.all(
    slots.map(async (slot) => {
      try {
        const res = await fetch(slot.dataset.include, { cache: "no-store" });
        const html = await res.text();
        slot.innerHTML = html;
        slot.removeAttribute("data-include");
      } catch (error) {
        slot.innerHTML = "";
      }
    })
  );
}

async function init() {
  try {
    state.config = await apiFetch("/api/bootstrap");
  } catch (error) {
    console.error(error);
  }

  renderCases();
  renderUpgradeChances();
  renderRarityFilters();
  renderGiveaways();
  renderTopPlayers();
  renderAccountStats();
  renderInventory();
  renderUpgradeInventory();
  renderUpgradeTargets();
  renderLiveFeed();

  setupNav();
  setupModals();
  setupClaimBonus();
  setupAuth();
  setupUpgrade();
  setupNotifications();
  renderNotifications();

  if (state.token) {
    try {
      const data = await apiFetch("/api/me");
      applyUser(data.user);
    } catch (error) {
      clearToken();
    }
  } else {
    renderBalance();
    renderNickname();
    updateBonusTimer();
  }

  startFeedPolling();
  startGiveawayTimers();
  startNotificationsPolling();
  setInterval(updateBonusTimer, 1000);
}

async function apiFetch(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) {
    headers.Authorization = `Bearer ${state.token}`;
  }

  const response = await fetch(path, {
    ...options,
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined
  });

  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    clearToken();
  }
  if (!response.ok) {
    const message = data.detail || "Ошибка запроса";
    throw new Error(message);
  }
  return data;
}

function setToken(token) {
  state.token = token;
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

function clearToken() {
  setToken(null);
  state.user = null;
  renderBalance();
  renderNickname();
  renderInventory();
  renderAccountStats();
  renderUpgradeInventory();
  renderUpgradeTargets();
  renderNotifications();
}

function applyUser(user) {
  state.user = user;
  const ownedIds = new Set(user.inventory.filter((item) => item.status === "owned").map((item) => item.id));
  state.upgradeSelected = state.upgradeSelected.filter((id) => ownedIds.has(id));
  renderBalance();
  renderNickname();
  renderInventory();
  renderAccountStats();
  renderUpgradeInventory();
  renderUpgradeTargets();
  renderTopPlayers();
  updateBonusTimer();
  fetchNotifications();
}

function ensureAuth() {
  if (state.user) return true;
  document.getElementById("authModal").classList.add("active");
  return false;
}

function setupNav() {
  const nav = document.getElementById("mainNav");
  nav.addEventListener("click", (event) => {
    const btn = event.target.closest(".nav-btn");
    if (!btn) return;
    if (btn.dataset.auth && !state.user) {
      document.getElementById("authModal").classList.add("active");
      return;
    }
    goToPage(btn.dataset.page);
  });
}

function goToPage(page) {
  document.querySelectorAll(".nav-btn").forEach((el) => el.classList.remove("active"));
  const navBtn = document.querySelector(`.nav-btn[data-page="${page}"]`);
  if (navBtn) {
    navBtn.classList.add("active");
  }

  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.id === `page-${page}`);
  });
}

function setupModals() {
  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.close;
      document.getElementById(target).classList.remove("active");
    });
  });

  document.getElementById("balanceBtn").addEventListener("click", () => {
    if (!ensureAuth()) return;
    document.getElementById("balanceModal").classList.add("active");
    updateBonusTimer();
  });

  document.getElementById("authBtn").addEventListener("click", () => {
    if (state.user) {
      goToPage("account");
      return;
    }
    document.getElementById("authModal").classList.add("active");
    document.getElementById("nicknameInput").value = "";
  });
}

function setupNotifications() {
  const notifyBtn = document.getElementById("notifyBtn");
  notifyBtn.addEventListener("click", () => {
    if (!ensureAuth()) return;
    document.getElementById("notificationsModal").classList.add("active");
    renderNotifications();
  });
}

function setupClaimBonus() {
  document.getElementById("claimBonus").addEventListener("click", async () => {
    if (!ensureAuth()) return;
    try {
      const data = await apiFetch("/api/balance/claim", { method: "POST" });
      applyUser(data.user);
    } catch (error) {
      updateBonusTimer();
    }
  });
}

function setupAuth() {
  document.getElementById("saveNickname").addEventListener("click", async () => {
    const value = document.getElementById("nicknameInput").value.trim();
    if (!value) return;
    try {
      const data = await apiFetch("/api/auth/login", {
        method: "POST",
        body: { nickname: value }
      });
      setToken(data.token);
      applyUser(data.user);
      document.getElementById("authModal").classList.remove("active");
    } catch (error) {
      alert(error.message);
    }
  });
}

function renderBalance() {
  const balance = state.user ? state.user.balance : 0;
  document.getElementById("balanceValue").textContent = formatPrice(balance);
  const modalValue = document.getElementById("balanceModalValue");
  if (modalValue) {
    modalValue.textContent = `${formatPrice(balance)} ₽`;
  }
}

function renderNickname() {
  const authBtn = document.getElementById("authBtn");
  if (!authBtn) return;
  if (state.user) {
    authBtn.textContent = "Аккаунт";
    authBtn.title = state.user.nickname;
  } else {
    authBtn.textContent = "Войти";
    authBtn.removeAttribute("title");
  }
}

function renderCases() {
  const grid = document.getElementById("caseGrid");
  if (!grid) return;
  grid.innerHTML = "";

  const cases = state.config?.cases || [];
  cases.forEach((item) => {
    const card = document.createElement("div");
    card.className = "case-card";
    card.innerHTML = `
      <div class="case-image">
        <img data-slug="${item.image_slug}" data-name="${item.name}" alt="" />
      </div>
      <div class="case-info">
        <div class="case-price">${formatPrice(item.price)} ₽</div>
      </div>
      <button class="primary-btn" data-case="${item.id}">Открыть</button>
    `;
    grid.appendChild(card);
  });

  grid.querySelectorAll("img[data-slug]").forEach((img) => {
    applyCaseImage(img, img.dataset.slug, img.dataset.name);
  });

  grid.querySelectorAll("button[data-case]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!ensureAuth()) return;
      openCaseModal(btn.dataset.case);
    });
  });
}

async function openCaseModal(caseId) {
  const item = state.config?.cases.find((caseItem) => caseItem.id === caseId);
  if (!item) return;
  state.selectedCase = caseId;

  document.getElementById("caseModalPrice").textContent = `${formatPrice(item.price)} ₽`;
  const image = document.getElementById("caseModalImage");
  applyCaseImage(image, item.image_slug, item.name);

  document.getElementById("caseResult").textContent = "";
  document.getElementById("caseModal").classList.add("active");
  document.getElementById("openCaseBtn").onclick = () => spinCase(item.id);

  if (!state.caseWeapons[caseId]) {
    try {
      const data = await apiFetch(`/api/cases/${caseId}/weapons`);
      state.caseWeapons[caseId] = data.weapons || [];
    } catch (error) {
      state.caseWeapons[caseId] = [];
    }
  }
  renderCaseWeapons(caseId);
}

function renderCaseWeapons(caseId) {
  const container = document.getElementById("caseWeapons");
  container.innerHTML = "";
  const legend = document.getElementById("weaponLegend");
  legend.innerHTML = "";

  (state.config?.rarities || []).forEach((rarity) => {
    const legendItem = document.createElement("div");
    legendItem.className = "legend-item";
    legendItem.style.background = rarity.color;
    legendItem.textContent = rarity.label;
    legend.appendChild(legendItem);
  });

  const weapons = state.caseWeapons[caseId] || [];
  const sorted = [...weapons].sort((a, b) => rarityIndex(a.rarity) - rarityIndex(b.rarity));

  sorted.forEach((weapon) => {
    const card = document.createElement("div");
    card.className = "weapon-card";
    card.innerHTML = `
      ${weapon.stattrak ? "<div class=\"stattrak\">StatTrak</div>" : ""}
      <div class="weapon-art" data-weapon="${weapon.name}"></div>
      <div style="color:${rarityColor(weapon.rarity)}">${rarityLabel(weapon.rarity)}</div>
      <div class="weapon-price">${formatPrice(weapon.price)} ₽</div>
    `;
    container.appendChild(card);
  });

  applyWeaponImages(container);
}

async function spinCase(caseId) {
  if (!ensureAuth()) return;
  const caseItem = state.config?.cases.find((caseData) => caseData.id === caseId);
  if (!caseItem) return;

  document.getElementById("caseResult").textContent = "Крутим...";

  let response;
  try {
    response = await apiFetch("/api/case/open", { method: "POST", body: { case_id: caseId } });
  } catch (error) {
    document.getElementById("caseResult").textContent = error.message;
    return;
  }

  const drop = response.drop;
  applyUser(response.user);

  const rollTrack = document.getElementById("rollTrack");
  rollTrack.innerHTML = "";
  rollTrack.style.transition = "none";
  rollTrack.style.transform = "translateX(0)";

  const weapons = state.caseWeapons[caseId] || [];
  const spinItems = buildSpinItems(weapons, drop);
  spinItems.items.forEach((weapon) => {
    const div = document.createElement("div");
    div.className = "roll-item";
    div.innerHTML = `
      <div class="weapon-art tiny" data-weapon="${weapon.name}"></div>
      <div class="rarity" style="color:${rarityColor(weapon.rarity)}">${rarityLabel(weapon.rarity)}</div>
      <div>${formatPrice(weapon.price)} ₽</div>
    `;
    rollTrack.appendChild(div);
  });

  applyWeaponImages(rollTrack);

  requestAnimationFrame(() => {
    rollTrack.style.transition = "transform 5.2s cubic-bezier(0.1, 0.7, 0.1, 1)";
    rollTrack.style.transform = `translateX(-${spinItems.offset}px)`;
  });

  rollTrack.addEventListener(
    "transitionend",
    () => {
      showCaseResult(drop, response.case_price);
      updateLiveFeed(drop, state.user?.nickname);
    },
    { once: true }
  );
}

function buildSpinItems(weapons, drop) {
  const spinCount = 38;
  const winnerIndex = spinCount - 6 - Math.floor(Math.random() * 4);
  const items = [];

  for (let i = 0; i < spinCount; i += 1) {
    if (i === winnerIndex) {
      items.push(drop);
    } else if (weapons.length) {
      items.push(randomFrom(weapons));
    } else {
      items.push(drop);
    }
  }
  const itemWidth = 130;
  const offset = winnerIndex * itemWidth - 260;
  return { items, offset };
}

function showCaseResult(weapon, casePrice) {
  const result = document.getElementById("caseResult");
  const label = weapon.stattrak ? "StatTrak" : rarityLabel(weapon.rarity);
  result.innerHTML = `
    <div class="result-info">
      <div class="weapon-art sm" data-weapon="${weapon.name}"></div>
      <div>
        <div>${label}</div>
        <div>${formatPrice(weapon.price)} ₽</div>
      </div>
    </div>
    <div class="actions">
      <button class="secondary-btn" id="sellDrop">Продать</button>
      <button class="primary-btn" id="openAgain">Еще раз (${formatPrice(casePrice)} ₽)</button>
    </div>
  `;

  applyWeaponImages(result);

  document.getElementById("sellDrop").addEventListener("click", async () => {
    try {
      const data = await apiFetch("/api/item/sell", { method: "POST", body: { item_id: weapon.id } });
      applyUser(data.user);
      result.innerHTML = "Лут продан и добавлен в баланс.";
    } catch (error) {
      result.textContent = error.message;
    }
  });

  document.getElementById("openAgain").addEventListener("click", () => {
    spinCase(state.selectedCase);
  });
}

function renderUpgradeChances() {
  const container = document.getElementById("upgradeChances");
  if (!container) return;
  const chances = [75, 50, 30, 25, 15];
  container.innerHTML = "";
  chances.forEach((chance) => {
    const btn = document.createElement("button");
    btn.className = "secondary-btn" + (state.upgradeChance === chance ? " active" : "");
    btn.textContent = `${chance}%`;
    btn.dataset.chance = chance;
    container.appendChild(btn);
  });

  container.addEventListener("click", (event) => {
    const btn = event.target.closest("button");
    if (!btn) return;
    state.upgradeChance = Number(btn.dataset.chance);
    container.querySelectorAll("button").forEach((el) => el.classList.remove("active"));
    btn.classList.add("active");
    updateUpgradeTargets();
  });
}

function renderUpgradeInventory() {
  const container = document.getElementById("upgradeInventory");
  if (!container) return;
  container.innerHTML = "";

  if (!state.user) {
    container.innerHTML = "<div class='inventory-item'>Войдите, чтобы выбрать оружия</div>";
    return;
  }

  const ownedItems = state.user.inventory.filter((item) => item.status === "owned");
  if (!ownedItems.length) {
    container.innerHTML = "<div class='inventory-item'>Пока пусто</div>";
    return;
  }

  ownedItems.forEach((item) => {
    const checked = state.upgradeSelected.includes(item.id) ? "checked" : "";
    const row = document.createElement("label");
    row.className = "inventory-item";
    row.innerHTML = `
      <span class="inventory-row">
        <input type="checkbox" data-id="${item.id}" ${checked} />
        <div class="weapon-art sm" data-weapon="${item.name}"></div>
        <span style="color:${rarityColor(item.rarity)}">${rarityLabel(item.rarity)}</span>
      </span>
      <span>${formatPrice(item.price)} ₽</span>
    `;
    container.appendChild(row);
  });

  applyWeaponImages(container);

  container.onchange = () => {
    const selected = Array.from(container.querySelectorAll("input:checked")).map((input) => input.dataset.id);
    state.upgradeSelected = selected;
    updateUpgradeTargets();
  };
}

function renderUpgradeTargets() {
  const container = document.getElementById("upgradeTargets");
  if (!container) return;
  container.innerHTML = "";
  const value = upgradeValue();
  if (!value) {
    container.innerHTML = "<div class='inventory-item'>Выберите оружия слева</div>";
    state.upgradeTarget = null;
    updateWheel();
    return;
  }

  state.upgradeTargets.forEach((item) => {
    const row = document.createElement("div");
    row.className = "inventory-item";
    row.innerHTML = `
      <span class="inventory-row">
        <div class="weapon-art sm" data-weapon="${item.name}"></div>
        <span style="color:${rarityColor(item.rarity)}">${rarityLabel(item.rarity)}</span>
      </span>
      <span>${formatPrice(item.price)} ₽</span>
    `;
    row.dataset.id = item.id;
    row.addEventListener("click", () => {
      state.upgradeTarget = item;
      container.querySelectorAll(".inventory-item").forEach((el) => el.classList.remove("active"));
      row.classList.add("active");
    });
    container.appendChild(row);
  });

  applyWeaponImages(container);
}

async function updateUpgradeTargets() {
  const value = upgradeValue();
  const upgradeValueEl = document.getElementById("upgradeValue");
  if (upgradeValueEl) {
    upgradeValueEl.textContent = `${formatPrice(value)} ₽`;
  }
  const chanceValueEl = document.getElementById("upgradeChanceValue");
  if (chanceValueEl) {
    chanceValueEl.textContent = `${state.upgradeChance}%`;
  }
  updateWheel();

  if (!state.user) {
    state.upgradeTargets = [];
    renderUpgradeTargets();
    return;
  }
  if (!value) {
    state.upgradeTargets = [];
    renderUpgradeTargets();
    return;
  }

  try {
    const data = await apiFetch("/api/upgrade/targets", {
      method: "POST",
      body: { item_ids: state.upgradeSelected, chance: state.upgradeChance }
    });
    state.upgradeTargets = data.targets || [];
  } catch (error) {
    state.upgradeTargets = [];
  }
  state.upgradeTarget = null;
  renderUpgradeTargets();
}

function setupUpgrade() {
  document.getElementById("startUpgrade").addEventListener("click", async () => {
    if (!ensureAuth()) return;
    if (!state.upgradeSelected.length || !state.upgradeTarget) {
      document.getElementById("upgradeResult").textContent = "Выберите свои и целевое оружие.";
      return;
    }

    try {
      const data = await apiFetch("/api/upgrade/start", {
        method: "POST",
        body: {
          item_ids: state.upgradeSelected,
          target_id: state.upgradeTarget.id,
          chance: state.upgradeChance
        }
      });

      applyUser(data.user);
      if (data.success) {
        document.getElementById("upgradeResult").textContent = "Успех! Оружие добавлено.";
        updateLiveFeed(data.reward, state.user?.nickname);
      } else {
        document.getElementById("upgradeResult").textContent = `Неудача. Компенсация ${formatPrice(data.consolation)} ₽.`;
      }
      state.upgradeSelected = [];
      state.upgradeTarget = null;
      renderUpgradeInventory();
      updateUpgradeTargets();
    } catch (error) {
      document.getElementById("upgradeResult").textContent = error.message;
    }
  });
}

async function renderGiveaways() {
  const container = document.getElementById("giveawaysGrid");
  if (!container) return;
  container.innerHTML = "";
  try {
    const data = await apiFetch("/api/giveaways");
    state.giveaways = data.giveaways || [];
  } catch (error) {
    state.giveaways = [];
  }

  state.giveaways.forEach((giveaway) => {
    const card = document.createElement("div");
    card.className = "giveaway-card";
    card.innerHTML = `
      <div class="giveaway-meta">
        <span>Вход: ${formatPrice(giveaway.entry)} ₽</span>
        <span data-timer="${giveaway.id}"></span>
      </div>
      <div class="giveaway-reward">
        <div class="reward-art" data-weapon="${giveaway.reward.name}"></div>
        <div>
          <div>${rarityLabel(giveaway.reward.rarity)}${giveaway.reward.stattrak ? " · StatTrak" : ""}</div>
          <div>${formatPrice(giveaway.reward.price)} ₽</div>
        </div>
      </div>
      <button class="primary-btn" data-join="${giveaway.id}">Участвовать</button>
    `;
    container.appendChild(card);
  });

  applyWeaponImages(container);

  container.querySelectorAll("button[data-join]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!ensureAuth()) return;
      try {
        const data = await apiFetch("/api/giveaways/join", {
          method: "POST",
          body: { giveaway_id: btn.dataset.join }
        });
        applyUser(data.user);
        btn.textContent = "Участвуешь";
      } catch (error) {
        btn.textContent = error.message;
      }
    });
  });
}

function startGiveawayTimers() {
  setInterval(() => {
    state.giveaways.forEach((giveaway) => {
      const timerEl = document.querySelector(`[data-timer="${giveaway.id}"]`);
      if (timerEl) {
        timerEl.textContent = `Старт через ${formatDuration(giveaway.start * 1000 - Date.now())}`;
      }
    });
  }, 1000);
}

async function renderTopPlayers() {
  const container = document.getElementById("topPlayers");
  if (!container) return;
  container.innerHTML = "";

  let players = [];
  try {
    const data = await apiFetch("/api/top");
    players = data.players || [];
  } catch (error) {
    players = [];
  }

  players.forEach((player, index) => {
    const row = document.createElement("div");
    row.className = "top-row";
    row.innerHTML = `
      <div>#${index + 1} ${player.nickname}</div>
      <div>${formatPrice(player.total)} ₽</div>
    `;
    container.appendChild(row);
  });
}

function renderAccountStats() {
  const stats = document.getElementById("accountStats");
  if (!stats) return;
  stats.innerHTML = "";

  if (!state.user) {
    stats.innerHTML = "<div class='stat-card'>Войдите для статистики</div>";
    return;
  }

  const bestDrop = state.user.stats.best_drop;
  const bestUpgrade = state.user.stats.best_upgrade;

  const cards = [
    { label: "Открыто кейсов", value: state.user.stats.cases_opened },
    { label: "Выигрышных кейсов", value: state.user.stats.cases_won },
    { label: "Кейсов сегодня", value: state.user.stats.daily_cases },
    { label: "Апгрейдов", value: state.user.stats.upgrades },
    { label: "Апгрейд побед", value: state.user.stats.upgrade_wins },
    { label: "Макс. баланс", value: `${formatPrice(state.user.stats.max_balance)} ₽` }
  ];

  cards.forEach((card) => {
    const div = document.createElement("div");
    div.className = "stat-card";
    div.innerHTML = `<span>${card.label}</span><strong>${card.value}</strong>`;
    stats.appendChild(div);
  });

  if (bestDrop) {
    const dropCard = document.createElement("div");
    dropCard.className = "stat-card";
    dropCard.innerHTML = `
      <span>Лучший дроп</span>
      <div class="weapon-art sm" data-weapon="${bestDrop.name}"></div>
      <strong>${formatPrice(bestDrop.price)} ₽</strong>
    `;
    stats.appendChild(dropCard);
  }

  if (bestUpgrade) {
    const upgradeCard = document.createElement("div");
    upgradeCard.className = "stat-card";
    upgradeCard.innerHTML = `
      <span>Лучший апгрейд</span>
      <div class="weapon-art sm" data-weapon="${bestUpgrade.name}"></div>
      <strong>${formatPrice(bestUpgrade.price)} ₽</strong>
    `;
    stats.appendChild(upgradeCard);
  }

  applyWeaponImages(stats);
}

function renderRarityFilters() {
  const container = document.getElementById("rarityFilters");
  if (!container) return;
  container.innerHTML = "";

  const rarities = state.config?.rarities || [];
  rarities.forEach((rarity) => {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" value="${rarity.id}" checked />${rarity.label}`;
    container.appendChild(label);
  });

  container.addEventListener("change", () => renderInventory());
  document.getElementById("onlyOwned").addEventListener("change", () => renderInventory());
  document.getElementById("priceMin").addEventListener("input", () => renderInventory());
  document.getElementById("priceMax").addEventListener("input", () => renderInventory());
}

function renderInventory() {
  const container = document.getElementById("accountInventory");
  if (!container) return;
  container.innerHTML = "";
  if (!state.user) {
    container.innerHTML = "<div class='inventory-item'>Войдите, чтобы видеть инвентарь</div>";
    return;
  }

  const onlyOwned = document.getElementById("onlyOwned").checked;
  const selectedRarities = Array.from(document.querySelectorAll("#rarityFilters input:checked")).map(
    (input) => input.value
  );
  const rarityFilterEmpty = selectedRarities.length === 0;
  const min = Number(document.getElementById("priceMin").value) || 0;
  const max = Number(document.getElementById("priceMax").value) || Infinity;

  const items = state.user.inventory.filter((item) => {
    if (onlyOwned && item.status !== "owned") return false;
    if (!rarityFilterEmpty && !selectedRarities.includes(item.rarity)) return false;
    if (item.price < min || item.price > max) return false;
    return true;
  });

  if (!items.length) {
    container.innerHTML = "<div class='inventory-item'>Нет подходящих оружий.</div>";
    return;
  }

  items.forEach((item) => {
    const card = document.createElement("div");
    const disabled = item.status !== "owned";
    card.className = `inventory-card${disabled ? " disabled" : ""}`;
    card.innerHTML = `
      ${item.stattrak ? "<div class=\"stattrak\">StatTrak</div>" : ""}
      <div class="status">${statusLabel(item.status)}</div>
      <div class="weapon-art" data-weapon="${item.name}"></div>
      <div style="color:${rarityColor(item.rarity)}">${rarityLabel(item.rarity)}</div>
      <div class="weapon-price">${formatPrice(item.price)} ₽</div>
      <div class="inventory-actions">
        <button class="secondary-btn" data-sell="${item.id}" ${disabled ? "disabled" : ""} title="${statusLabel(item.status)}">Продать</button>
        <button class="secondary-btn" data-upgrade="${item.id}" ${disabled ? "disabled" : ""} title="${statusLabel(item.status)}">Апгрейд</button>
      </div>
    `;
    container.appendChild(card);
  });

  applyWeaponImages(container);

  container.querySelectorAll("button[data-sell]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const data = await apiFetch("/api/item/sell", { method: "POST", body: { item_id: btn.dataset.sell } });
        applyUser(data.user);
      } catch (error) {
        alert(error.message);
      }
    });
  });

  container.querySelectorAll("button[data-upgrade]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = state.user.inventory.find((weapon) => weapon.id === btn.dataset.upgrade);
      if (!item || item.status !== "owned") return;
      goToPage("upgrade");
      state.upgradeSelected = [item.id];
      renderUpgradeInventory();
      updateUpgradeTargets();
    });
  });
}

function renderLiveFeed() {
  const container = document.getElementById("liveFeed");
  if (!container) return;
  container.innerHTML = "";
  state.feed.slice(0, 10).forEach((item) => {
    appendLiveFeed(container, item.nickname, item.weapon);
  });
}

async function startFeedPolling() {
  await fetchFeed();
  setInterval(fetchFeed, 6500);
}

async function fetchFeed() {
  try {
    const data = await apiFetch("/api/feed");
    state.feed = data.items || [];
    renderLiveFeed();
  } catch (error) {
    // ignore
  }
}

function updateLiveFeed(drop, nickname) {
  const container = document.getElementById("liveFeed");
  appendLiveFeed(container, nickname || "Игрок", drop.name, true);
}

function appendLiveFeed(container, nickname, weaponName, prepend = false) {
  const item = document.createElement("div");
  item.className = "live-item";
  item.innerHTML = `
    <div class="live-weapon" data-weapon="${weaponName}"></div>
    <span class="live-nick">${nickname}</span>
  `;
  if (prepend) {
    container.prepend(item);
    if (container.children.length > 10) {
      container.removeChild(container.lastChild);
    }
  } else {
    container.appendChild(item);
  }
  applyWeaponImages(item);
}

function updateBonusTimer() {
  const timer = document.getElementById("bonusTimer");
  const claimBtn = document.getElementById("claimBonus");
  if (!timer || !claimBtn) return;
  if (!state.user) {
    timer.textContent = "Войдите для бонуса";
    claimBtn.disabled = true;
    claimBtn.classList.add("cooldown");
    return;
  }
  const cooldown = 20 * 60 * 1000;
  const remaining = cooldown - (Date.now() - state.user.last_claim * 1000);
  if (remaining <= 0) {
    timer.textContent = "";
    claimBtn.disabled = false;
    claimBtn.classList.remove("cooldown");
    return;
  }
  timer.textContent = formatDuration(remaining);
  claimBtn.disabled = true;
  claimBtn.classList.add("cooldown");
}

function updateWheel() {
  const wheel = document.getElementById("upgradeWheel");
  if (!wheel) return;
  const chance = state.upgradeChance;
  wheel.style.background = `conic-gradient(var(--success) 0deg ${chance * 3.6}deg, rgba(148, 163, 184, 0.2) ${chance * 3.6}deg 360deg)`;
}

function upgradeValue() {
  if (!state.user) return 0;
  return state.user.inventory
    .filter((item) => state.upgradeSelected.includes(item.id))
    .reduce((sum, item) => sum + item.price, 0);
}

async function fetchNotifications() {
  if (!state.user) {
    state.notifications = [];
    renderNotifications();
    return;
  }
  try {
    const data = await apiFetch("/api/notifications");
    state.notifications = data.notifications || [];
  } catch (error) {
    state.notifications = [];
  }
  renderNotifications();
}

function renderNotifications() {
  const list = document.getElementById("notificationsList");
  const badge = document.getElementById("notifyCount");
  if (!list || !badge) return;

  const upcoming = state.notifications.filter((item) => item.status === "upcoming");
  badge.textContent = upcoming.length;
  badge.style.display = upcoming.length ? "inline-block" : "none";

  list.innerHTML = "";
  if (!state.user) {
    list.innerHTML = "<div class='notification-item'>Войдите, чтобы видеть уведомления</div>";
    return;
  }
  if (!state.notifications.length) {
    list.innerHTML = "<div class='notification-item'>Пока пусто</div>";
    return;
  }

  state.notifications.forEach((item) => {
    const row = document.createElement("div");
    row.className = "notification-item";
    const timeLeft = item.start * 1000 - Date.now();
    row.innerHTML = `
      <div class="inventory-row">
        <div class="weapon-art sm" data-weapon="${item.reward.name}"></div>
        <div>
          <div>${rarityLabel(item.reward.rarity)}${item.reward.stattrak ? " · StatTrak" : ""}</div>
          <div class="meta">${formatPrice(item.entry)} ₽</div>
        </div>
      </div>
      <div class="meta">${item.status === "upcoming" ? formatDuration(timeLeft) : "Завершен"}</div>
    `;
    list.appendChild(row);
  });

  applyWeaponImages(list);
}

function startNotificationsPolling() {
  setInterval(() => {
    if (state.user) {
      fetchNotifications();
    }
  }, 20000);
}

function rarityLabel(id) {
  return state.config?.rarities?.find((rarity) => rarity.id === id)?.label || id;
}

function rarityColor(id) {
  return state.config?.rarities?.find((rarity) => rarity.id === id)?.color || "#94a3b8";
}

function rarityIndex(id) {
  return state.config?.rarities?.findIndex((rarity) => rarity.id === id) ?? 0;
}

function statusLabel(status) {
  switch (status) {
    case "owned":
      return "В наличии";
    case "sold":
      return "Продано";
    case "upgraded":
      return "Апгрейд";
    case "failed":
      return "Проиграно";
    default:
      return "";
  }
}

function formatPrice(value) {
  return Math.round(value).toLocaleString("ru-RU");
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function randomFrom(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function slugify(value) {
  return value
    .toLowerCase()
    .trim()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9\-]/g, "")
    .replace(/-+/g, "-");
}

function applyCaseImage(img, slug, name = "") {
  let index = 0;
  let phase = 0;
  img.style.display = "block";

  const tryNext = () => {
    if (index >= IMAGE_EXTS.length) {
      if (phase === 0 && name) {
        phase = 1;
        index = 0;
      } else {
        img.style.display = "none";
        return;
      }
    }
    const ext = IMAGE_EXTS[index];
    const base = phase === 0 ? slug : encodeURIComponent(name);
    img.src = `case/${base}.${ext}`;
    index += 1;
  };

  img.onerror = tryNext;
  tryNext();
}

function applyWeaponImages(container) {
  const elements = container.querySelectorAll("[data-weapon]");
  elements.forEach((el) => {
    applyWeaponImage(el, el.dataset.weapon);
  });
}

function applyWeaponImage(element, name) {
  if (!name) return;
  const slug = slugify(name);
  const candidates = [];
  if (slug) {
    IMAGE_EXTS.forEach((ext) => candidates.push(`guns/${slug}.${ext}`));
  }
  IMAGE_EXTS.forEach((ext) => candidates.push(`guns/${encodeURIComponent(name)}.${ext}`));
  if (!candidates.length) return;

  const probe = new Image();
  let index = 0;

  const tryNext = () => {
    if (index >= candidates.length) return;
    probe.src = candidates[index];
    index += 1;
  };

  probe.onload = () => {
    element.style.backgroundImage = `url('${probe.src}')`;
  };
  probe.onerror = tryNext;
  tryNext();
}
