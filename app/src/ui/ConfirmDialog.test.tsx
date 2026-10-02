import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { ConfirmDialog, useConfirm } from "./ConfirmDialog";

const base = { title: "Удалить запись?", message: "Звук и расшифровка будут удалены.", confirmLabel: "Удалить" };

test("focus starts on the safe button, Enter there cancels", async () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(<ConfirmDialog {...base} onConfirm={onConfirm} onCancel={onCancel} />);
  const dialog = screen.getByRole("alertdialog", { name: "Удалить запись?" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveAccessibleDescription("Звук и расшифровка будут удалены.");
  expect(screen.getByRole("button", { name: "Отмена" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();
});

test("Esc cancels the modal wherever focus is", () => {
  const onCancel = vi.fn();
  render(<ConfirmDialog {...base} onConfirm={() => {}} onCancel={onCancel} />);
  fireEvent.keyDown(document.body, { key: "Escape" });
  expect(onCancel).toHaveBeenCalledTimes(1);
});

test("the action button is styled as danger and confirms", async () => {
  const onConfirm = vi.fn();
  render(<ConfirmDialog {...base} onConfirm={onConfirm} onCancel={() => {}} />);
  const action = screen.getByRole("button", { name: "Удалить" });
  expect(action).toHaveClass("btn--danger");
  await userEvent.click(action);
  expect(onConfirm).toHaveBeenCalledTimes(1);
});

test("Tab stays inside the modal", async () => {
  render(<><button type="button">снаружи</button>
    <ConfirmDialog {...base} alt={{ label: "Не сохранять", onClick: () => {} }} onConfirm={() => {}} onCancel={() => {}} /></>);
  const cancel = screen.getByRole("button", { name: "Отмена" });
  const action = screen.getByRole("button", { name: "Удалить" });
  expect(cancel).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByRole("button", { name: "Не сохранять" })).toHaveFocus();
  await userEvent.tab();
  expect(action).toHaveFocus();
  await userEvent.tab();
  expect(cancel).toHaveFocus();
  await userEvent.tab({ shift: true });
  expect(action).toHaveFocus();
});

test("clicking the backdrop cancels; clicking inside does not", () => {
  const onCancel = vi.fn();
  render(<ConfirmDialog {...base} onConfirm={() => {}} onCancel={onCancel} />);
  fireEvent.mouseDown(screen.getByRole("alertdialog"));
  expect(onCancel).not.toHaveBeenCalled();
  fireEvent.mouseDown(document.querySelector(".confirm-backdrop")!);
  expect(onCancel).toHaveBeenCalledTimes(1);
});

test("focus returns to the opener after closing", async () => {
  function Host() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>Удалить…</button>
        {open && <ConfirmDialog {...base} onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)} />}
      </>
    );
  }
  render(<Host />);
  const opener = screen.getByRole("button", { name: "Удалить…" });
  await userEvent.click(opener);
  expect(screen.getByRole("button", { name: "Отмена" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(opener).toHaveFocus();
});

test("inline variant: no backdrop, Esc inside cancels, safe button focused", () => {
  const onCancel = vi.fn();
  render(<ConfirmDialog {...base} inline onConfirm={() => {}} onCancel={onCancel} />);
  expect(document.querySelector(".confirm-backdrop")).toBeNull();
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).not.toHaveAttribute("aria-modal");
  expect(screen.getByRole("button", { name: "Отмена" })).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(onCancel).toHaveBeenCalledTimes(1);
});

test("modal: the rest of the page is inert while it is open, focus can rest on the box itself", () => {
  const outside = document.createElement("div");
  outside.innerHTML = "<button>за окном</button>";
  document.body.appendChild(outside);
  const already = document.createElement("div");
  already.setAttribute("inert", "");
  document.body.appendChild(already);
  try {
    const { unmount } = render(<ConfirmDialog {...base} onConfirm={() => {}} onCancel={() => {}} />);
    expect(outside).toHaveAttribute("inert");
    expect(document.querySelector(".confirm-backdrop")).not.toHaveAttribute("inert");
    expect(screen.getByRole("alertdialog")).toHaveAttribute("tabindex", "-1");
    unmount();
    expect(outside).not.toHaveAttribute("inert");
    expect(already).toHaveAttribute("inert"); // чужой inert не трогаем
  } finally {
    outside.remove();
    already.remove();
  }
});

test("focus returns after an inline confirm and to returnFocus when the opener is gone", async () => {
  function Host() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>Удалить модель…</button>
        {open && <ConfirmDialog {...base} inline onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)} />}
      </>
    );
  }
  render(<Host />);
  const opener = screen.getByRole("button", { name: "Удалить модель…" });
  await userEvent.click(opener);
  await userEvent.keyboard("{Escape}");
  expect(opener).toHaveFocus();

  function Menu() {
    const more = useRef<HTMLButtonElement>(null);
    const [menu, setMenu] = useState(false);
    const [ask, setAsk] = useState(false);
    return (
      <>
        <button type="button" ref={more} onClick={() => setMenu(true)}>Ещё</button>
        {menu && <button type="button" onClick={() => { setMenu(false); setAsk(true); }}>Перерасшифровать…</button>}
        {ask && <ConfirmDialog {...base} returnFocus={more} onConfirm={() => setAsk(false)} onCancel={() => setAsk(false)} />}
      </>
    );
  }
  render(<Menu />);
  await userEvent.click(screen.getByRole("button", { name: "Ещё" }));
  await userEvent.click(screen.getByRole("button", { name: "Перерасшифровать…" }));
  await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
  expect(screen.getByRole("button", { name: "Ещё" })).toHaveFocus();
});

test("modal: inert is lifted before focus goes back (Chromium ignores focus inside inert)", async () => {
  // Как в Chromium: focus() внутри [inert] не срабатывает.
  const original = HTMLElement.prototype.focus;
  const spy = vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (this: HTMLElement, opts) {
    if (this.closest("[inert]")) return;
    original.call(this, opts);
  });
  try {
    function Host() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Удалить запись…</button>
          {open && <ConfirmDialog {...base} onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)} />}
        </>
      );
    }
    render(<Host />);
    const opener = screen.getByRole("button", { name: "Удалить запись…" });
    await userEvent.click(opener);
    expect(opener.closest("[inert]")).not.toBeNull(); // страница за окном недоступна
    await userEvent.keyboard("{Escape}");
    expect(opener.closest("[inert]")).toBeNull();
    expect(opener).toHaveFocus();
    await userEvent.click(opener);
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(opener).toHaveFocus();
  } finally {
    spy.mockRestore();
  }
});

test("non-destructive confirm uses the primary style", () => {
  render(<ConfirmDialog {...base} danger={false} onConfirm={() => {}} onCancel={() => {}} />);
  expect(screen.getByRole("button", { name: "Удалить" })).toHaveClass("btn--primary");
});

test("useConfirm resolves true on confirm and false on cancel", async () => {
  const results: boolean[] = [];
  function Host() {
    const [node, confirm] = useConfirm();
    return (
      <>
        <button type="button" onClick={async () => { results.push(await confirm(base)); }}>спросить</button>
        {node}
      </>
    );
  }
  render(<Host />);
  await userEvent.click(screen.getByRole("button", { name: "спросить" }));
  await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
  await userEvent.click(screen.getByRole("button", { name: "спросить" }));
  await act(async () => { fireEvent.keyDown(document.body, { key: "Escape" }); });
  expect(results).toEqual([true, false]);
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test("stacked modals: Esc closes only the top one, the page stays inert until the last closes", () => {
  const bottomCancel = vi.fn();
  const topCancel = vi.fn();
  function Stack({ top, bottom }: { top: boolean; bottom: boolean }) {
    return (
      <>
        {bottom && <ConfirmDialog title="Отменить правки?" confirmLabel="Отменить правки" onConfirm={() => {}} onCancel={bottomCancel} />}
        {top && <ConfirmDialog title="Уйти из настроек?" confirmLabel="Уйти" onConfirm={() => {}} onCancel={topCancel} />}
      </>
    );
  }
  const { container, rerender } = render(<Stack top={false} bottom />);
  rerender(<Stack top bottom />);
  fireEvent.keyDown(document.body, { key: "Escape" });
  expect(topCancel).toHaveBeenCalledTimes(1);
  expect(bottomCancel).not.toHaveBeenCalled();
  // Нижнее закрыли первым — страница под верхним всё ещё inert.
  rerender(<Stack top bottom={false} />);
  expect(container).toHaveAttribute("inert");
  rerender(<Stack top={false} bottom={false} />);
  expect(container).not.toHaveAttribute("inert");
  fireEvent.keyDown(document.body, { key: "Escape" });
  expect(bottomCancel).not.toHaveBeenCalled();
});
