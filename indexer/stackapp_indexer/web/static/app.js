/* StackApp UI glue. No framework, no build step, no bundler.
 *
 * The server builds unsigned Solana transaction messages in Python and sends
 * them here base58-encoded. The wallet extension signs and submits. No private
 * key ever reaches the server, and nothing here can see one either.
 */

const Wallet = {
  provider: null,
  pubkey: null,

  detect() {
    const w = window;
    if (w.phantom?.solana?.isPhantom) return w.phantom.solana;
    if (w.solflare?.isSolflare) return w.solflare;
    if (w.solana) return w.solana;
    return null;
  },

  async connect() {
    this.provider = this.detect();
    if (!this.provider) {
      throw new Error(
        "No Solana wallet found. Install Phantom or Solflare and set it to Devnet."
      );
    }
    const res = await this.provider.connect();
    this.pubkey = (res?.publicKey ?? this.provider.publicKey)?.toString();
    if (!this.pubkey) throw new Error("Wallet did not return a public key.");
    localStorage.setItem("stackapp:wallet", this.pubkey);
    this.render();
    return this.pubkey;
  },

  async disconnect() {
    try { await this.provider?.disconnect(); } catch { /* ignore */ }
    this.pubkey = null;
    localStorage.removeItem("stackapp:wallet");
    this.render();
  },

  async ensure() {
    if (this.pubkey) return this.pubkey;
    return this.connect();
  },

  render() {
    const slot = document.getElementById("wallet-slot");
    if (!slot) return;
    if (this.pubkey) {
      slot.innerHTML = `
        <span class="wallet-addr">${short(this.pubkey)}</span>
        <button class="btn-ghost" id="wallet-disconnect">Disconnect</button>`;
      document.getElementById("wallet-disconnect").onclick = () => Wallet.disconnect();
    } else {
      slot.innerHTML = `<button class="btn-primary" id="wallet-connect">Connect wallet</button>`;
      document.getElementById("wallet-connect").onclick = () =>
        Wallet.connect().catch((e) => toast(e.message, "err"));
    }
    document.querySelectorAll("[data-needs-wallet]").forEach((el) => {
      el.disabled = !this.pubkey;
    });
    document.dispatchEvent(new CustomEvent("wallet", { detail: this.pubkey }));
  },

  /** Ask the wallet to sign and submit a server-built message. */
  async signAndSend(messageB58) {
    await this.ensure();
    // Phantom and Solflare both accept a base58 message through `request`.
    const result = await this.provider.request({
      method: "signAndSendTransaction",
      params: { message: messageB58 },
    });
    return result?.signature ?? result;
  },
};

function short(key, lead = 4, tail = 4) {
  if (!key) return "-";
  return key.length <= lead + tail + 1 ? key : `${key.slice(0, lead)}…${key.slice(-tail)}`;
}

function toast(message, kind = "info") {
  const host = document.getElementById("toast");
  if (!host) return;
  host.className = `note ${kind}`;
  host.textContent = message;
  host.style.display = "block";
  if (kind === "ok") setTimeout(() => (host.style.display = "none"), 12000);
}

/**
 * Build an instruction on the server, sign it in the wallet, submit it.
 * `action` is one of the names in txbuild.BUILDERS.
 */
async function sendAction(action, params, button) {
  const original = button?.textContent;
  try {
    if (button) { button.disabled = true; button.textContent = "Signing…"; }
    const wallet = await Wallet.ensure();

    const response = await fetch(`/api/tx/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ wallet, ...params }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? `server returned ${response.status}`);

    const signature = await Wallet.signAndSend(payload.message);
    toast(`Confirmed. Signature ${short(signature, 8, 8)}`, "ok");
    document.dispatchEvent(new CustomEvent("tx", { detail: { action, signature } }));
    setTimeout(() => window.location.reload(), 1800);
    return signature;
  } catch (error) {
    toast(explain(error), "err");
    return null;
  } finally {
    if (button) { button.disabled = false; button.textContent = original; }
  }
}

/** Turn a raw failure into something a person can act on. */
function explain(error) {
  const raw = String(error?.message ?? error);
  const known = [
    [/ClaimTooSoon/, "Too soon to claim - a few slots must pass after any balance increase."],
    [/InsufficientSpendable/, "Not enough unlocked balance. Claim vested tokens first."],
    [/NothingToClaim/, "Nothing to claim right now."],
    [/LotCapacityExceeded/, "This position is at its lot limit. Compact lots first."],
    [/NotMatured/, "This position has not been held long enough yet."],
    [/ExitedBeforeMaturity/, "Too much of this position was sold for it to count as matured."],
    [/SlippageExceeded/, "Price moved past your limit. Try again."],
    [/TaxCurveNotMonotonic/, "The tax curve may never rise with time held."],
    [/insufficient lamports|Attempt to debit/, "Not enough devnet SOL. Airdrop some first."],
    [/User rejected|rejected the request/i, "Rejected in the wallet."],
    [/not been deployed|invalid program|ProgramAccountNotFound/i,
      "The program is not deployed on devnet yet. Run anchor deploy first."],
  ];
  for (const [pattern, message] of known) if (pattern.test(raw)) return message;
  return raw;
}

/* -- live feed ------------------------------------------------------------ */

function connectFeed(onEvent, onStatus) {
  let socket = null, closed = false, backoff = 1000, timer = null;

  const open = () => {
    if (closed) return;
    onStatus?.("connecting");
    socket = new WebSocket(`ws://${location.host}/ws`);
    socket.onopen = () => { backoff = 1000; onStatus?.("open"); };
    socket.onmessage = (msg) => {
      try {
        const payload = JSON.parse(msg.data);
        if (payload.type === "event") onEvent(payload.event);
      } catch { /* ignore malformed frames */ }
    };
    socket.onclose = () => {
      onStatus?.("closed");
      if (closed) return;
      timer = setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    socket.onerror = () => socket?.close();
  };

  open();
  return () => { closed = true; clearTimeout(timer); socket?.close(); };
}

/* -- boot ----------------------------------------------------------------- */

document.addEventListener("DOMContentLoaded", () => {
  // Reconnect silently if this browser has approved the site before.
  const remembered = localStorage.getItem("stackapp:wallet");
  Wallet.render();
  if (remembered) {
    const provider = Wallet.detect();
    provider?.connect({ onlyIfTrusted: true })
      .then((res) => {
        Wallet.provider = provider;
        Wallet.pubkey = (res?.publicKey ?? provider.publicKey)?.toString();
        Wallet.render();
      })
      .catch(() => { /* the user has to click Connect */ });
  }
});
