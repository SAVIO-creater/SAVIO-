const forms = document.querySelector(".form");
const addButton = document.querySelector(".mber_btn");
const existhp = document.querySelector(".exist");
const homepage = document.querySelector(".homepage");

const createbtn = document.querySelector("#createbtn");

const firstname = document.getElementById("firstname");
const surname = document.getElementById("surname");
const number = document.getElementById("number");
const surety = document.getElementById("surety");
const deposite = document.getElementById("deposite");
const status = document.getElementById("status");

const API = "http://127.0.0.1:8000";

function formatNumber(number) {
    const value = Number(number);
    if (!Number.isFinite(value)) {
        return "0";
    }
    return value.toLocaleString();
}

function safeNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
}

// ADD MEMBERS
addButton.addEventListener("click", function () {
    hideAllPages();
    forms.style.display = "block";
});


// CANCEL
existhp.addEventListener("click", function () {
    hideAllPages();
    homepage.style.display = "flex";
});


// CREATE MEMBER
createbtn.addEventListener("click", async function (event) {
    event.preventDefault();

    const member_info = {
        firstname: firstname.value,
        surname: surname.value,
        number: number.value,
        surety: surety.value,
        deposite: deposite.value,
        status: status.value
    };

    if (
        firstname.value === "" ||
        surname.value === "" ||
        number.value === "" ||
        surety.value === "" ||
        deposite.value === "" ||
        status.value === ""
    ) {
        alert("please insert all values");
        return;
    }

    try {
        const response = await fetch(`${API}/member`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(member_info)
        });

        await response.json();
        alert("Member saved!");
        firstname.value = "";
        surname.value = "";
        number.value = "";
        surety.value = "";
        deposite.value = "";
        forms.style.display = "none";
        homepage.style.display = "block";
        await loadMembers();
        await loadGroupStatus();
    } catch (error) {
        console.error(error);
        alert("Could not connect to SAVIO backend.");
    }
});


//VIEW BUTTON DECK
const viewpaga = document.getElementById("viewpaga");
const view_btn = document.querySelector(".view_btn");

const memberSearch = document.getElementById("memberSearch");
const membersEmpty = document.getElementById("members-empty");
const membersCount = document.getElementById("members-count");
let allMembers = [];
let activeMemberFilter = "all";

function normalizeStatus(status) {
    const value = String(status || "").trim().toUpperCase();
    if (value === "SECERETARY") {
        return "SECRETARY";
    }
    return value;
}

function memberMatchesQuery(member, query) {
    if (!query) {
        return true;
    }

    const haystack = [
        member[0],
        member[1],
        member[2],
        `${member[1]} ${member[2]}`,
        member[3],
        member[4],
        member[6]
    ].join(" ").toLowerCase();

    return haystack.includes(query);
}

function filteredMembers() {
    const query = (memberSearch.value || "").trim().toLowerCase();

    return allMembers.filter((member) => {
        const status = normalizeStatus(member[6]);
        const matchesRole = activeMemberFilter === "all" || status === activeMemberFilter;
        return matchesRole && memberMatchesQuery(member, query);
    });
}

function renderMembers() {
    const container = document.querySelector("#members-container");
    const template = document.querySelector("#viewpaga template");
    const members = filteredMembers();

    container.innerHTML = "";

    if (!members.length) {
        membersEmpty.style.display = "block";
        membersCount.textContent = allMembers.length
            ? "0 members match this search"
            : "No members yet";
        return;
    }

    membersEmpty.style.display = "none";
    membersCount.textContent = `${members.length} of ${allMembers.length} member${allMembers.length === 1 ? "" : "s"}`;

    members.forEach((member) => {
        const card = template.content.cloneNode(true);

        card.querySelector(".cardname").dataset.memberId = member[0];
        card.querySelector(".mberindex").textContent = member[0];
        card.querySelector(".mbercard").textContent = `${member[1]} ${member[2]}`;
        card.querySelector(".mbernumber").textContent = member[3];
        card.querySelector(".mberstatus").textContent = member[6];
        card.querySelector(".mberbalance").textContent = formatNumber(member[5]);

        container.appendChild(card);
    });
}

async function loadMembers() {
    try {
        const response = await fetch(`${API}/members`);
        allMembers = await response.json();
        renderMembers();
    } catch (error) {
        console.error(error);
        allMembers = [];
        renderMembers();
        alert("Could not load members.");
    }
}

function openMembersPage() {
    hideAllPages();
    viewpaga.style.display = "flex";
    loadMembers();
}

view_btn.addEventListener("click", openMembersPage);

const exit = document.querySelector(".exit");
exit.addEventListener("click", function () {
    hideAllPages();
    homepage.style.display = "flex";
});

document.querySelectorAll(".member-chip").forEach((chip) => {
    chip.addEventListener("click", function () {
        document.querySelectorAll(".member-chip").forEach((item) => item.classList.remove("is-active"));
        this.classList.add("is-active");
        activeMemberFilter = this.dataset.filter;
        renderMembers();
    });
});

memberSearch.addEventListener("input", renderMembers);

loadMembers();


//CREATING AN ACCOUNTPAGE
const accountpage = document.getElementById("accountpage");
let selectedMemberId = null;

async function loadAccount(memberId) {
    const loanResponse = await fetch(`${API}/account/${memberId}/loan`);
    const loanData = await loanResponse.json();

    const loanAmount = loanData.loan ? safeNumber(loanData.loan[2]) : 0;
    const loanInterest = loanData.loan ? safeNumber(loanData.loan[3]) : 0;
    const interestAmount = loanAmount * (loanInterest / 100);
    const totalLoan = loanAmount + interestAmount;

    document.querySelector(".accountloan").textContent = formatNumber(totalLoan);

    const response = await fetch(`${API}/account/${memberId}`);
    const data = await response.json();
    const member = data.member;

    document.querySelector(".accountname").textContent = `${member[1]} ${member[2]}`;
    document.querySelector(".accountindex").textContent = member[0];
    document.querySelector(".accountsavings").textContent = formatNumber(member[5]);

    const welfareResponse = await fetch(`${API}/account/${memberId}/welfare`);
    const welfareData = await welfareResponse.json();
    const welfareTotal = safeNumber(welfareData.welfare);
    document.querySelector(".welfarefee").textContent = formatNumber(welfareTotal);

    const fineResponse = await fetch(`${API}/account/${memberId}/fine`);
    const fineData = await fineResponse.json();
    const fineTotal = safeNumber(fineData.fine);
    document.querySelector(".penaltyfee").textContent = formatNumber(fineTotal);

    document.querySelector(".AvailableBalance").textContent = formatNumber(
        safeNumber(member[5]) - totalLoan - fineTotal
    );
}

document.addEventListener("click", async function (event) {
    const card = event.target.closest(".cardname");
    if (!card) return;

    selectedMemberId = card.dataset.memberId;
    await loadAccount(selectedMemberId);

    viewpaga.style.display = "none";
    accountpage.style.display = "block";
});


//ACCOUNT FORMS
const depositbtn = document.querySelector(".depositbtn");
const depositform = document.getElementById("depositform");
const returnbutton = document.querySelector(".returnbutton");
const Depositdown = document.querySelector(".Depositdown");
const welfareform = document.getElementById("welfareform");
const welfarebtn = document.querySelector(".welfarebtn");
const welfaredown = document.querySelector(".welfaredown");
const loanform = document.getElementById("loanform");
const loanbtn = document.querySelector(".loanbtn");
const loandown = document.querySelector(".loandown");
const fineform = document.getElementById("fineform");
const finebtn = document.querySelector(".finebtn");
const finedown = document.querySelector(".finedown");
const Depositup = document.querySelector(".Depositup");
const depositamount = document.getElementById("depositamount");
const payform = document.getElementById("payform");
const payamount = document.getElementById("payamount");
const payup = document.querySelector(".payup");
const paydown = document.querySelector(".paydown");
const paybtn = document.querySelector(".paybtn");

returnbutton.addEventListener("click", function () {
    accountpage.style.display = "none";
    viewpaga.style.display = "block";
    loadMembers();
});

depositbtn.addEventListener("click", function () {
    depositform.style.display = "block";
    accountpage.style.display = "none";
});
Depositdown.addEventListener("click", function () {
    depositform.style.display = "none";
    accountpage.style.display = "block";
});

welfarebtn.addEventListener("click", function () {
    welfareform.style.display = "block";
    accountpage.style.display = "none";
});
welfaredown.addEventListener("click", function () {
    welfareform.style.display = "none";
    accountpage.style.display = "block";
});

loanbtn.addEventListener("click", function () {
    loanform.style.display = "block";
    accountpage.style.display = "none";
});
loandown.addEventListener("click", function () {
    loanform.style.display = "none";
    accountpage.style.display = "block";
});

finebtn.addEventListener("click", function () {
    fineform.style.display = "block";
    accountpage.style.display = "none";
});
finedown.addEventListener("click", function () {
    fineform.style.display = "none";
    accountpage.style.display = "block";
});

paybtn.addEventListener("click", function () {
    payform.style.display = "block";
    accountpage.style.display = "none";
});
paydown.addEventListener("click", function () {
    payform.style.display = "none";
    accountpage.style.display = "block";
});


//deposit form DECK
Depositup.addEventListener("click", async function () {
    const amount = Number(depositamount.value);

    if (!amount) {
        alert("Please insert the amount");
        return;
    }

    const response = await fetch(`${API}/account/${selectedMemberId}/deposit`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            amount: amount
        })
    });

    await response.json();

    if (response.ok) {
        depositamount.value = "";
        depositform.style.display = "none";
        accountpage.style.display = "block";
        await loadAccount(selectedMemberId);
        await loadGroupStatus();
        alert("BALANCE UPGRADED");
    } else {
        alert("BALANCE DIDN'T UPGRADE");
    }
});


//loan form DECK
const loanup = document.querySelector(".loanup");
const loanpercent = document.getElementById("loanpercent");
const loanamount = document.getElementById("loanamount");

loanup.addEventListener("click", async function () {
    if (loanpercent.value === "" || loanamount.value === "") {
        alert("insert all the data");
        return;
    }

    const amount = Number(loanamount.value);
    const interest = Number(loanpercent.value);

    const response = await fetch(`${API}/account/${selectedMemberId}/loan`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            amount: amount,
            interest: interest
        })
    });

    await response.json();

    if (response.ok) {
        loanamount.value = "";
        loanpercent.value = "";
        alert("Loan Successfully Uploaded");
        loanform.style.display = "none";
        accountpage.style.display = "block";
        await loadAccount(selectedMemberId);
        await loadGroupStatus();
    } else {
        alert("Process Failed");
    }
});


//welfare form DECK
const WelfareAmount = document.getElementById("WelfareAmount");
const welfareup = document.querySelector(".welfareup");

welfareup.addEventListener("click", async function () {
    if (WelfareAmount.value === "") {
        alert("Please insert the amount");
        return;
    }

    const amount = Number(WelfareAmount.value);

    const response = await fetch(`${API}/account/${selectedMemberId}/welfare`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            amount: amount
        })
    });

    await response.json();

    if (response.ok) {
        WelfareAmount.value = "";
        welfareform.style.display = "none";
        accountpage.style.display = "block";
        await loadAccount(selectedMemberId);
        await loadGroupStatus();
        alert("Welfare amount upgraded");
    } else {
        alert("Welfare did not upgrade");
    }
});


//payment form DECK
payup.addEventListener("click", async function () {
    const amount = Number(payamount.value);

    if (!selectedMemberId) {
        alert("Please select a member first");
        return;
    }

    if (!amount || amount <= 0) {
        alert("Please insert the payment amount");
        return;
    }

    try {
        const response = await fetch(`${API}/account/${selectedMemberId}/payment`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                amount: amount
            })
        });

        const result = await response.json();

        if (response.ok) {
            payamount.value = "";
            payform.style.display = "none";
            accountpage.style.display = "block";
            await loadAccount(selectedMemberId);
            await loadGroupStatus();
            alert(result.message || "Loan payment saved");
        } else {
            alert(result.message || "Payment failed");
        }
    } catch (error) {
        console.error(error);
        alert("Could not connect to SAVIO backend.");
    }
});


//fine form DECK
const fineup = document.querySelector(".fineup");
const fineAmountInput = document.querySelector("#fineform input");
const fineReasonInput = document.querySelector("#fineform textarea");

fineup.addEventListener("click", async function () {
    if (!fineAmountInput.value) {
        alert("Please insert the amount");
        return;
    }

    const amount = Number(fineAmountInput.value);
    const reason = fineReasonInput.value || "";

    const response = await fetch(`${API}/account/${selectedMemberId}/fine`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            amount: amount,
            reason: reason
        })
    });

    await response.json();

    if (response.ok) {
        fineAmountInput.value = "";
        fineReasonInput.value = "";
        fineform.style.display = "none";
        accountpage.style.display = "block";
        await loadAccount(selectedMemberId);
        await loadGroupStatus();
        alert("Fine uploaded");
    } else {
        alert("Fine did not upgrade");
    }
});


//ACCOUNT VIEWING
const accountView = document.getElementById("accountView");
const exitaccount = document.querySelector(".exitacntviw");
const accountbutton = document.querySelector(".acnt_btn");

accountbutton.addEventListener("click", function () {
    hideAllPages();
    accountView.style.display = "block";
    loadGroupStatus();
});

exitaccount.addEventListener("click", function () {
    hideAllPages();
    homepage.style.display = "flex";
});

async function loadGroupStatus() {
    try {
        const savings = await fetch(`${API}/group/status`).then(res => res.json());
        document.querySelector(".totalsavings").textContent = formatNumber(savings.total_savings);

        const loans = await fetch(`${API}/group/loan-status`).then(res => res.json());
        document.querySelector(".totalloan").textContent = formatNumber(loans.total_loans);

        const balance = await fetch(`${API}/group/balance-status`).then(res => res.json());
        document.querySelector(".avaisavings").textContent = formatNumber(balance.balance);

        const welfare = await fetch(`${API}/group/welfare-status`).then(res => res.json());
        document.querySelector(".totalwelfare").textContent = formatNumber(welfare.total_welfare);

        const fines = await fetch(`${API}/group/fine-status`).then(res => res.json());
        document.querySelector(".totalfine").textContent = formatNumber(fines.total_fines);

        const profits = await fetch(`${API}/group/profits-status`).then(res => res.json());
        document.querySelector(".totalprofit").textContent = formatNumber(profits.total_profit);
    } catch (error) {
        console.error(error);
    }

    loadAIPanel();
}
loadGroupStatus();

const RecordView = document.getElementById("RecordView");
const recordsList = document.getElementById("records-list");
const recordsEmpty = document.getElementById("records-empty");
const recordTemplate = document.getElementById("record-card-template");
const searchInput = document.getElementById("search");

let allRecords = [];
let activeFilter = "all";

const RECORD_META = {
    savings: { label: "Savings", icon: "fa-piggy-bank" },
    loan: { label: "Loan", icon: "fa-hand-holding-dollar" },
    fine: { label: "Fine", icon: "fa-triangle-exclamation" },
    welfare: { label: "Welfare", icon: "fa-users" },
    interest: { label: "Interest", icon: "fa-coins" }
};

function hideAllPages() {
    document.querySelectorAll("body > section").forEach((section) => {
        section.style.display = "none";
    });
}

function formatRecordDate(value) {
    if (!value) {
        return "Date unavailable";
    }

    const date = new Date(String(value).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }

    const day = date.getDate();
    const month = date.toLocaleString("en-GB", { month: "short" });
    const year = date.getFullYear();
    const time = date.toLocaleString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: true
    });

    return `${day} ${month} ${year}  •  ${time}`;
}

function formatUGX(amount) {
    return `+ UGX ${formatNumber(amount)}`;
}

function filteredRecords() {
    const query = (searchInput.value || "").trim().toLowerCase();

    return allRecords.filter((record) => {
        const matchesType = activeFilter === "all" || record.type === activeFilter;
        const matchesName = !query || String(record.name || "").toLowerCase().includes(query);
        return matchesType && matchesName;
    });
}

function renderRecords() {
    const records = filteredRecords();
    recordsList.innerHTML = "";

    if (!records.length) {
        recordsEmpty.style.display = "block";
        return;
    }

    recordsEmpty.style.display = "none";

    records.forEach((record) => {
        const card = recordTemplate.content.cloneNode(true);
        const article = card.querySelector(".record-card");
        const meta = RECORD_META[record.type] || RECORD_META.savings;

        article.dataset.type = record.type;
        article.dataset.memberId = record.member_id;
        article.querySelector(".record-icon i").className = `fa-solid ${meta.icon}`;
        article.querySelector(".record-name").textContent = record.name;
        article.querySelector(".record-type").textContent = meta.label;
        article.querySelector(".record-date span").textContent = formatRecordDate(record.created_at);
        article.querySelector(".record-amount").textContent = formatUGX(record.amount);

        recordsList.appendChild(card);
    });
}

recordsList.addEventListener("click", async function (event) {
    const card = event.target.closest(".record-card");
    if (!card || !card.dataset.memberId) return;

    selectedMemberId = card.dataset.memberId;
    hideAllPages();
    accountpage.style.display = "block";
    await loadAccount(selectedMemberId);
});

async function loadRecords() {
    try {
        const response = await fetch(`${API}/records`);
        allRecords = await response.json();
        renderRecords();
    } catch (error) {
        console.error(error);
        allRecords = [];
        renderRecords();
    }
}

function openRecordsPage() {
    hideAllPages();
    RecordView.style.display = "flex";
    loadRecords();
}

document.querySelector(".rcds_btn").addEventListener("click", openRecordsPage);

document.querySelectorAll(".filter-chip").forEach((chip) => {
    chip.addEventListener("click", function () {
        document.querySelectorAll(".filter-chip").forEach((item) => item.classList.remove("is-active"));
        this.classList.add("is-active");
        activeFilter = this.dataset.filter;
        renderRecords();
    });
});

searchInput.addEventListener("input", renderRecords);

document.querySelectorAll(".app-tabbar, .records-tabbar").forEach((bar) => {
    bar.addEventListener("click", function (event) {
        const button = event.target.closest(".tab-btn");
        if (!button) return;

        const dest = button.dataset.go;
        if (dest === "home") {
            hideAllPages();
            homepage.style.display = "flex";
        } else if (dest === "add") {
            hideAllPages();
            forms.style.display = "block";
        } else if (dest === "members") {
            openMembersPage();
        } else if (dest === "records") {
            openRecordsPage();
        } else if (dest === "account") {
            hideAllPages();
            accountView.style.display = "block";
            loadGroupStatus();
        }
    });
});

// ===== SAVIO AI =====
const ACTIVITY_ICON = {
    savings: { icon: "fa-piggy-bank", bg: "#1f8a22" },
    loan: { icon: "fa-hand-holding-dollar", bg: "#f08a14" },
    fine: { icon: "fa-triangle-exclamation", bg: "#e23b3b" },
    welfare: { icon: "fa-users", bg: "#2b6fe3" },
    interest: { icon: "fa-coins", bg: "#ef8b12" }
};

async function loadAIPanel() {
    try {
        const overview = await fetch(`${API}/ai/overview`).then(res => res.json());
        document.getElementById("aiTotalSavings").textContent = `UGX ${formatNumber(overview.total_savings)}`;
        document.getElementById("aiAvailable").textContent = `UGX ${formatNumber(overview.balance)}`;
        document.getElementById("aiTotalLoan").textContent = `UGX ${formatNumber(overview.total_loans)}`;
        document.getElementById("aiWelfare").textContent = `UGX ${formatNumber(overview.total_welfare)}`;
        document.getElementById("aiFines").textContent = `UGX ${formatNumber(overview.total_fines)}`;
        document.getElementById("aiProfit").textContent = `UGX ${formatNumber(overview.total_profit)}`;
    } catch (error) {
        console.error(error);
    }

    try {
        const insight = await fetch(`${API}/ai/insight`).then(res => res.json());
        document.getElementById("aiInsightText").textContent = insight.insight;
    } catch (error) {
        console.error(error);
    }

    try {
        const activity = await fetch(`${API}/ai/recent-activity?limit=3`).then(res => res.json());
        const list = document.getElementById("aiActivityList");
        list.innerHTML = "";

        if (!activity.length) {
            list.innerHTML = `<p class="ai-activity-empty">No activity yet.</p>`;
            return;
        }

        activity.forEach((record) => {
            const meta = ACTIVITY_ICON[record.type] || ACTIVITY_ICON.savings;
            const row = document.createElement("div");
            row.className = "ai-activity-row";
            row.innerHTML = `
                <div class="ai-activity-icon" style="background:${meta.bg}"><i class="fa-solid ${meta.icon}"></i></div>
                <div class="ai-activity-main">
                    <p class="ai-activity-name">${record.name}</p>
                    <p class="ai-activity-type">${record.type[0].toUpperCase() + record.type.slice(1)}</p>
                </div>
                <p class="ai-activity-amount" style="color:${meta.bg}">UGX ${formatNumber(record.amount)}</p>
            `;
            list.appendChild(row);
        });
    } catch (error) {
        console.error(error);
    }
}

document.querySelector(".savio-ai-viewall").addEventListener("click", openRecordsPage);

// ---- Ask SAVIO chat (SAVIO AI <-> Gemini) ----
const askSavioPage = document.getElementById("askSavioPage");
const chatMessages = document.getElementById("chatMessages");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const chatPageName = document.getElementById("chatPageName");
const chatPageSub = document.getElementById("chatPageSub");
const chatAvatar = document.getElementById("chatAvatar");
const modeButtons = document.querySelectorAll(".ai-mode-btn");

// Two personas, one backend: both are answered by Gemini, but SAVIO AI is grounded
// strictly in the group's data while Gemini mode is a general-purpose assistant.
// Each mode keeps its own transcript (what's shown) and history (what's sent for context).
let currentMode = "savio";
const chatState = {
    savio: {
        history: [],
        transcript: [],
        greeting: "Hi! I'm SAVIO AI. Ask me about savings, loans, members, or anything else about the group's finances - I'll reason it through using your group's real data.",
        name: "Ask SAVIO",
        sub: "Your group's financial assistant",
        icon: "fa-robot"
    },
    gemini: {
        history: [],
        transcript: [],
        greeting: "Hi, I'm Gemini - working alongside SAVIO. Ask me anything, group-related or not, and I'll focus on understanding what you actually need rather than just listing numbers.",
        name: "Ask Gemini",
        sub: "General assistant, powered by Gemini",
        icon: "fa-star-of-life"
    }
};

function renderBubble(role, text) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;
    bubble.textContent = text;
    chatMessages.appendChild(bubble);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return bubble;
}

// Adds a bubble to a given mode's transcript, but only paints it on screen
// if that mode is still the one currently open (guards against a reply
// landing after the user has already navigated to the other mode).
function appendBubble(mode, role, text) {
    chatState[mode].transcript.push({ role, text });
    return mode === currentMode ? renderBubble(role, text) : null;
}

function renderChat() {
    chatMessages.innerHTML = "";
    const state = chatState[currentMode];
    if (!state.transcript.length) {
        state.transcript.push({ role: "ai", text: state.greeting });
    }
    state.transcript.forEach((msg) => renderBubble(msg.role, msg.text));
}

function switchMode(mode) {
    if (mode === currentMode || !chatState[mode]) return;
    currentMode = mode;
    const state = chatState[currentMode];

    modeButtons.forEach((btn) => btn.classList.toggle("is-active", btn.dataset.mode === mode));
    chatPageName.textContent = state.name;
    chatPageSub.textContent = state.sub;
    chatAvatar.innerHTML = `<i class="fa-solid ${state.icon}"></i>`;

    renderChat();
}

modeButtons.forEach((btn) => btn.addEventListener("click", function () {
    switchMode(btn.dataset.mode);
}));

function openAskSavio() {
    hideAllPages();
    askSavioPage.style.display = "flex";
    renderChat();
}

document.querySelectorAll(".opensavio").forEach((btn) => btn.addEventListener("click", openAskSavio));
document.querySelector(".closeSavio").addEventListener("click", function () {
    hideAllPages();
    homepage.style.display = "flex";
});

chatForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    const question = chatInput.value.trim();
    if (!question) return;

    const modeAtSend = currentMode;
    const state = chatState[modeAtSend];

    appendBubble(modeAtSend, "user", question);
    chatInput.value = "";
    const thinking = appendBubble(modeAtSend, "ai", "Thinking...");
    state.transcript.pop(); // don't persist the placeholder in the transcript

    try {
        const response = await fetch(`${API}/ai/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: question, history: state.history, mode: modeAtSend })
        });
        const result = await response.json();

        if (thinking) thinking.remove();

        if (result.error) {
            appendBubble(modeAtSend, "error", result.answer);
            return;
        }

        appendBubble(modeAtSend, "ai", result.answer);
        state.history.push({ role: "user", content: question });
        state.history.push({ role: "assistant", content: result.answer });
    } catch (error) {
        console.error(error);
        if (thinking) thinking.remove();
        const otherMode = modeAtSend === "savio" ? "gemini" : "savio";
        const otherLabel = chatState[otherMode].name.replace("Ask ", "");
        appendBubble(
            modeAtSend,
            "error",
            `I'm having trouble getting a reply right now. Try again in a moment, or switch to ${otherLabel} above while this sorts itself out.`
        );
    }
});

// ---- Group Report ----
const groupReportPage = document.getElementById("groupReportPage");
const reportBody = document.getElementById("reportBody");

async function loadGroupReport() {
    reportBody.innerHTML = `<p class="report-loading">Generating report...</p>`;
    try {
        const response = await fetch(`${API}/ai/report`, { method: "POST" });
        const result = await response.json();

        if (result.error) {
            reportBody.innerHTML = `<p class="report-loading">${result.message}</p>`;
            return;
        }

        reportBody.textContent = result.report;
    } catch (error) {
        console.error(error);
        reportBody.innerHTML = `<p class="report-loading">Could not reach SAVIO AI. Check your connection and try again.</p>`;
    }
}

document.querySelectorAll(".openreport").forEach((btn) => btn.addEventListener("click", function () {
    hideAllPages();
    groupReportPage.style.display = "flex";
    loadGroupReport();
}));

document.querySelector(".closeReport").addEventListener("click", function () {
    hideAllPages();
    homepage.style.display = "flex";
});

document.getElementById("regenReport").addEventListener("click", loadGroupReport);
