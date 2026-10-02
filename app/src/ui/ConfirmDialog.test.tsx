import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
