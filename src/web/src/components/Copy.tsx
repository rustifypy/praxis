import { useEffect, useState } from "react";
import { Button } from "./Button";

export function Copy({ value, label = "Copy" }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!done) return;
    const timer = window.setTimeout(() => setDone(false), 1400);
    return () => window.clearTimeout(timer);
  }, [done]);

  return (
    <Button
      size="sm"
      className={`copy ${done ? "done" : ""}`}
      title={label}
      onClick={(e) => {
        e.stopPropagation();
        void navigator.clipboard.writeText(value).then(() => setDone(true));
      }}
    >
      {done ? "Copied" : label}
    </Button>
  );
}
