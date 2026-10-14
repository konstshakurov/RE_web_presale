import type { InputHTMLAttributes } from "react";

// Переключатель вкл/выкл (UX-1 п. 4): label.switch > input[type=checkbox][role=switch].
type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "role"> & { label: string };

export default function Switch({ label, className, ...rest }: Props) {
  return (
    <label className={className ? `switch ${className}` : "switch"}>
      <input type="checkbox" role="switch" {...rest} />
      {label}
    </label>
  );
}
