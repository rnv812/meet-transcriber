import { useId, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Icon } from "./Icon";
import "./primitives.css";

/**
 * Раскрывающийся блок вместо `<details>`: тот же шеврон, что и в остальном
 * окне (поворачивается при раскрытии), заголовок — кнопка с aria-expanded.
 */
export function Disclosure({ title, children, defaultOpen = false, open: controlled, onToggle, className = "" }: {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  className?: string;
}) {
  const [own, setOwn] = useState(defaultOpen);
  const open = controlled ?? own;
  const id = useId();
  const toggle = () => { setOwn(!open); onToggle?.(!open); };
  return (
    <div className={`disclosure${open ? " disclosure--open" : ""} ${className}`.trim()}>
      <button type="button" className="disclosure__head" aria-expanded={open} aria-controls={id} onClick={toggle}>
        <Icon as={ChevronRight} size="sm" className="disclosure__chevron" />
        <span>{title}</span>
      </button>
      {open && <div className="disclosure__body" id={id}>{children}</div>}
    </div>
  );
}
