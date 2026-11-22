// Invite codes (data-model.md): 8 characters without look-alikes, shown as K7QM-4XPA.
// Kept for this tab only (sessionStorage): closing the tab forgets it (open decision §12 #7,
// the lead leaned this way). The code goes to the API in a header, never in a URL.

const ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
const CODE = new RegExp(`^[${ALPHABET}]{8}$`);
const KEY = "itr.invite";
const NICK = "itr.nickname";

export function normalise(text: string | null | undefined): string | null {
  if (!text) return null;
  const code = text.replace(/[\s-]/g, "").toUpperCase();
  return CODE.test(code) ? code : null;
}

export function shown(code: string): string {
  return `${code.slice(0, 4)}-${code.slice(4)}`;
}

function store(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null; // blocked storage: the player types the code again
  }
}

export function savedCode(): string | null {
  return normalise(store()?.getItem(KEY));
}

export function saveCode(code: string): void {
  store()?.setItem(KEY, code);
}

export function forgetCode(): void {
  store()?.removeItem(KEY);
}

export function savedNickname(): string {
  return store()?.getItem(NICK) ?? "";
}

export function saveNickname(name: string): void {
  store()?.setItem(NICK, name);
}
