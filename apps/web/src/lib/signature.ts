// Email signatures: per person per mailbox (GET/PUT /mail/accounts/{id}/signature). Templates
// render table-based, inline-styled HTML that mail clients show the same everywhere and that
// the server's allow-list keeps. The company logo is <img src="cid:tt-logo">: the server embeds
// the picture in the sent email; in the app it is shown from /mail/signature-logo.
import { apiRequest as request } from "../api";

export const LOGO_URL = "/api/v1/mail/signature-logo";
export const DEFAULT_ADDRESS = "140 Milner Ave., Units 34-36, Scarborough, ON M1S 3R3";

export type TemplateId = "classic" | "modern" | "minimal" | "compact" | "centered" | "logotop";
export type SigFields = {
  mode: "template" | "custom";
  template: TemplateId;
  name: string;
  title: string;
  department: string;
  company: string;
  phone: string;
  mobile: string;
  email: string;
  website: string;
  address: string;
  linkedin: string;
  facebook: string;
  instagram: string;
  showLogo: boolean;
  logoSize: number;
  accent: string;
  font: string;
  fontSize: number;
  disclaimer: string;
  customHtml: string;
};
export type SignatureOut = { html: string; fields: Partial<SigFields>; use_on_new: boolean; use_on_reply: boolean; mailbox_default_html: string; logo_cid: string };

export const signatures = {
  get: (acc: string) => request<SignatureOut>("GET", `/mail/accounts/${acc}/signature`),
  put: (acc: string, b: { html: string; fields: SigFields; use_on_new: boolean; use_on_reply: boolean }) => request<SignatureOut>("PUT", `/mail/accounts/${acc}/signature`, b),
};

export const TEMPLATES: { id: TemplateId; label: string; hint: string }[] = [
  { id: "classic", label: "Classic", hint: "Logo on the left, details beside it" },
  { id: "modern", label: "Modern", hint: "Accent bar and bold name" },
  { id: "minimal", label: "Minimal", hint: "Text only" },
  { id: "compact", label: "Compact", hint: "One or two lines" },
  { id: "centered", label: "Centered card", hint: "Centred with a top rule" },
  { id: "logotop", label: "Logo on top", hint: "Logo above the details" },
];

// Brand colours offered for the accent (rgb() so they pass the inline-CSS allow-list).
export const ACCENTS = [
  { label: "Tamisha green", value: "rgb(27, 122, 48)" },
  { label: "Fresh green", value: "rgb(40, 167, 69)" },
  { label: "Forest", value: "rgb(9, 65, 22)" },
  { label: "Ink", value: "rgb(24, 24, 27)" },
  { label: "Slate", value: "rgb(107, 107, 116)" },
  { label: "Blue", value: "rgb(36, 89, 198)" },
];
export const SIG_FONTS = [
  { label: "Arial", css: "Arial, Helvetica, sans-serif" },
  { label: "Georgia", css: "Georgia, 'Times New Roman', serif" },
  { label: "Verdana", css: "Verdana, Geneva, sans-serif" },
  { label: "Trebuchet", css: "'Trebuchet MS', sans-serif" },
  { label: "Tahoma", css: "Tahoma, Geneva, sans-serif" },
];

export function defaultFields(name: string, email: string): SigFields {
  return {
    mode: "template",
    template: "classic",
    name,
    title: "",
    department: "",
    company: "Tamisha Trading Inc.",
    phone: "",
    mobile: "",
    email,
    website: "tamishatrading.com",
    address: DEFAULT_ADDRESS,
    linkedin: "",
    facebook: "",
    instagram: "",
    showLogo: true,
    logoSize: 72,
    accent: ACCENTS[0]?.value ?? "rgb(27, 122, 48)",
    font: SIG_FONTS[0]?.css ?? "Arial, Helvetica, sans-serif",
    fontSize: 13,
    disclaimer: "",
    customHtml: "",
  };
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const tel = (s: string) => `tel:${s.replace(/[^\d+]/g, "")}`;
const web = (s: string) => (/^https?:\/\//i.test(s) ? s : `https://${s}`);
/** Colour as typed: rgb()/hex from the picker; anything else falls back to the brand green. */
export const safeColor = (c: string) => (/^(#[0-9a-f]{3,8}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\))$/i.test(c.trim()) ? c.trim() : "rgb(27, 122, 48)");

/** The signature HTML for a template (logo as cid:tt-logo). */
export function renderSignature(f: SigFields): string {
  if (f.mode === "custom") return f.customHtml;
  const accent = safeColor(f.accent);
  const size = Math.max(10, Math.min(18, Math.round(f.fontSize) || 13));
  const font = SIG_FONTS.find((x) => x.css === f.font)?.css ?? SIG_FONTS[0]?.css ?? "Arial, sans-serif";
  const base = `font-family: ${font}; font-size: ${size}px; line-height: 1.45; color: rgb(51, 51, 51)`;
  const muted = "color: rgb(110, 110, 110)";
  const logoW = Math.max(32, Math.min(200, Math.round(f.logoSize) || 72));
  const logo = f.showLogo ? `<img src="cid:tt-logo" width="${logoW}" alt="${esc(f.company || "Tamisha Trading")}" style="width: ${logoW}px; height: auto; border: 0">` : "";
  const role = [f.title, f.department].filter(Boolean).map(esc).join(" · ");
  const contact: string[] = [];
  if (f.phone) contact.push(`<a href="${esc(tel(f.phone))}" style="color: rgb(51, 51, 51); text-decoration: none">T ${esc(f.phone)}</a>`);
  if (f.mobile) contact.push(`<a href="${esc(tel(f.mobile))}" style="color: rgb(51, 51, 51); text-decoration: none">M ${esc(f.mobile)}</a>`);
  if (f.email) contact.push(`<a href="mailto:${esc(f.email)}" style="color: ${accent}; text-decoration: none">${esc(f.email)}</a>`);
  if (f.website) contact.push(`<a href="${esc(web(f.website))}" style="color: ${accent}; text-decoration: none">${esc(f.website.replace(/^https?:\/\//i, ""))}</a>`);
  const socials = (
    [
      ["LinkedIn", f.linkedin],
      ["Facebook", f.facebook],
      ["Instagram", f.instagram],
    ] as const
  )
    .filter(([, u]) => u)
    .map(([l, u]) => `<a href="${esc(web(u))}" style="color: ${accent}; text-decoration: none">${l}</a>`);
  const name = `<span style="font-size: ${size + 3}px; font-weight: bold; color: rgb(24, 24, 27)">${esc(f.name)}</span>`;
  const company = f.company ? `<span style="font-weight: bold; color: ${accent}">${esc(f.company)}</span>` : "";
  const sep = ` <span style="color: rgb(190, 190, 190)">|</span> `;
  const lines = [
    role && `<span style="${muted}">${role}</span>`,
    company,
    contact.length ? contact.join(sep) : "",
    f.address && `<span style="${muted}">${esc(f.address)}</span>`,
    socials.length ? socials.join(sep) : "",
  ].filter(Boolean);
  const disclaimer = f.disclaimer ? `<div style="margin-top: 10px; font-size: ${Math.max(10, size - 3)}px; ${muted}">${esc(f.disclaimer)}</div>` : "";
  const td = (content: string, style = "") => `<td style="vertical-align: top; ${style}">${content}</td>`;
  const table = (row: string, style = "") => `<table cellpadding="0" cellspacing="0" border="0" style="border-collapse: collapse; ${base}; ${style}"><tr>${row}</tr></table>`;
  const stack = (items: string[]) => items.map((l) => `<div>${l}</div>`).join("");

  switch (f.template) {
    case "modern":
      return `<div>${table(td("", `width: 4px; background-color: ${accent}`) + td(stack([name, ...lines]), "padding: 2px 0 2px 12px") + (logo ? td(logo, "padding: 2px 0 0 16px") : ""))}${disclaimer}</div>`;
    case "minimal":
      return `<div style="${base}">${stack([`<span style="font-weight: bold">${esc(f.name)}</span>`, ...lines])}${disclaimer}</div>`;
    case "compact": {
      const one = [`<span style="font-weight: bold">${esc(f.name)}</span>`, role && `<span style="${muted}">${role}</span>`, company].filter(Boolean).join(sep);
      return `<div style="${base}"><div>${one}</div>${contact.length ? `<div>${contact.join(sep)}</div>` : ""}${disclaimer}</div>`;
    }
    case "centered":
      return `<div>${table(td(stack([logo, name, ...lines].filter(Boolean)), `text-align: center; padding: 10px 18px; border-top: 3px solid ${accent}`))}${disclaimer}</div>`;
    case "logotop":
      return `<div style="${base}">${logo ? `<div style="margin-bottom: 8px">${logo}</div>` : ""}${stack([name, ...lines])}${disclaimer}</div>`;
    default:
      return `<div>${table((logo ? td(logo, `padding: 0 14px 0 0; border-right: 2px solid ${accent}`) : "") + td(stack([name, ...lines]), logo ? "padding: 0 0 0 14px" : ""))}${disclaimer}</div>`;
  }
}

/** For showing in the app: the stored logo reference (cid or the server's data-cid) → the logo URL. */
export const sigForDisplay = (html: string) => html.replace(/data-cid="tt-logo"/g, `src="${LOGO_URL}"`).replace(/src="cid:tt-logo"/g, `src="${LOGO_URL}"`);
/** For sending/saving: the logo URL back to cid:tt-logo (the server embeds the picture). */
export const sigForWire = (html: string) => html.split(`src="${LOGO_URL}"`).join('src="cid:tt-logo"');

/** "rgb(27, 122, 48)" → "#1b7a30" for the colour picker (hex kept as is). */
export function toHex(c: string): string {
  if (c.startsWith("#")) return c;
  const n = (c.match(/\d{1,3}/g) ?? ["0", "0", "0"]).slice(0, 3).map((x) => Math.max(0, Math.min(255, Number(x))));
  return "#" + n.map((x) => x.toString(16).padStart(2, "0")).join("");
}
