// Tags every visible form control with data-aa-id and returns one entry per question.
// Radios/checkboxes sharing a name collapse into one question with options.
() => {
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  // Lever and Greenhouse mark required questions with a trailing * or ✱ instead of the attribute.
  const marked = s => /[*✱]$/.test(s);
  const unmark = s => s.replace(/\s*[*✱]$/, "");
  const visible = el => {
    if (el.type === "file") return true;              // file inputs are often hidden behind a button
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return st.display !== "none" && st.visibility !== "hidden" && (r.width > 0 || r.height > 0 || el.type === "radio" || el.type === "checkbox");
  };
  const labelFor = el => {
    if (el.getAttribute("aria-labelledby")) {
      const t = el.getAttribute("aria-labelledby").split(" ").map(id => document.getElementById(id)?.innerText).join(" ");
      if (clean(t)) return clean(t);
    }
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    // Walk up to the field's container and take its label/legend/heading text.
    let n = el.parentElement;
    for (let i = 0; n && i < 6; i++, n = n.parentElement) {
      const l = n.querySelector(":scope > label, :scope > legend, :scope > .application-label, :scope > div > label, :scope > fieldset > legend");
      if (l && !l.contains(el) && clean(l.innerText)) return clean(l.innerText);
      if (n.tagName === "LABEL") return clean(n.innerText);
    }
    return clean(el.placeholder || el.name || el.id);
  };
  const groupLabel = el => {
    const fs = el.closest("fieldset");
    if (fs) {
      const lg = fs.querySelector("legend");
      if (lg && clean(lg.innerText)) return clean(lg.innerText);
    }
    let n = el.parentElement;
    for (let i = 0; n && i < 6; i++, n = n.parentElement) {
      if (n.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1 || i > 2) {
        const l = n.querySelector(":scope > label, :scope > .application-label, :scope > div > label, :scope > p, :scope > span");
        if (l && !l.contains(el) && clean(l.innerText)) return clean(l.innerText);
      }
    }
    return clean(el.name);
  };
  // Greenhouse labels file inputs only with their "Attach" button; the real name sits on a
  // labelled ancestor (aria-labelledby="upload-label-resume") or in the input's id.
  const fileLabel = el => {
    const box = el.closest("[aria-labelledby]");
    if (box) {
      const t = box.getAttribute("aria-labelledby").split(" ").map(id => document.getElementById(id)?.innerText).join(" ");
      if (clean(t)) return clean(t);
    }
    return clean((el.id || el.name || "").replace(/[_-]+/g, " "));
  };
  const optionText = el => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return clean(l.innerText);
    }
    const l = el.closest("label");
    return clean(l ? l.innerText : el.value);
  };

  const out = [];
  const groups = {};
  let n = 0;
  document.querySelectorAll("input, textarea, select").forEach(el => {
    const type = (el.type || "").toLowerCase();
    if (["hidden", "submit", "button", "reset", "image", "search"].includes(type)) return;
    if (!visible(el) || el.disabled) return;
    // react-select keeps a hidden required mirror input next to the real combobox; skip it.
    if (type !== "file" && (el.getAttribute("aria-hidden") === "true" || el.tabIndex === -1)) return;
    const id = "aa" + (n++);
    el.setAttribute("data-aa-id", id);
    let required = el.required || el.getAttribute("aria-required") === "true";

    if ((type === "radio" || type === "checkbox") && el.name) {
      const key = type + ":" + el.name;
      if (!groups[key]) {
        const raw = groupLabel(el);
        groups[key] = { id, kind: type, label: unmark(raw), name: el.name, options: [], required: required || marked(raw) };
        out.push(groups[key]);
      }
      groups[key].options.push({ id, text: unmark(optionText(el)) });
      groups[key].required ||= required;
      return;
    }
    let raw = labelFor(el);
    if (type === "file" && /^(attach|upload|browse|choose file|select file)?$/i.test(raw)) raw = fileLabel(el) || raw;
    const f = { id, label: unmark(raw), required: required || marked(raw) };
    if (type === "file") f.kind = "file";
    else if (el.tagName === "SELECT") { f.kind = "select"; f.options = [...el.options].map(o => clean(o.text)).filter(Boolean); }
    else if (el.tagName === "TEXTAREA") f.kind = "textarea";
    else if (el.getAttribute("role") === "combobox" || el.closest("[class*=select__control]")) f.kind = "combobox";
    else if (type === "checkbox") f.kind = "checkbox_single";
    else f.kind = "text";
    if (f.kind === "checkbox_single") f.options = [{ id, text: unmark(optionText(el)) }];
    out.push(f);
  });
  return out;
}
