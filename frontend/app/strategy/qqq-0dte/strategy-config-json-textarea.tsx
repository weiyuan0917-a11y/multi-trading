"use client";

import { useCallback, useRef, useState } from "react";
import { splitStrategyConfigLineComment } from "./strategy-config-json-hints";

type Props = {
  value: string;
  onChange: (next: string) => void;
  className?: string;
  minHeightClass?: string;
  spellCheck?: boolean;
  placeholder?: string;
};

/**
 * 与原生 textarea 同值编辑；底层用 pre 叠加层为行尾 `  # … #` 注释着色（正文 textarea 为透明字色）。
 */
export function StrategyConfigJsonTextarea({
  value,
  onChange,
  className = "",
  minHeightClass = "min-h-[140px]",
  spellCheck = false,
  placeholder,
}: Props) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [scroll, setScroll] = useState({ top: 0, left: 0 });

  const onScroll = useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    setScroll({ top: el.scrollTop, left: el.scrollLeft });
  }, []);

  const lines = value.split("\n");

  const boxClass =
    "relative w-full overflow-hidden rounded-xl border border-slate-600 bg-[rgba(2,6,23,0.7)] focus-within:border-sky-400 focus-within:shadow-[0_0_0_3px_rgba(56,189,248,0.2)]";

  const innerPad = "px-3 py-[0.55rem]";
  const fontClass = "font-mono text-xs leading-relaxed whitespace-pre-wrap break-words";

  return (
    <div className={`${boxClass} ${className}`}>
      <pre
        className={`pointer-events-none absolute inset-0 m-0 overflow-hidden ${innerPad} ${fontClass}`}
        aria-hidden
      >
        <code
          className="block text-slate-300"
          style={{ transform: `translate(${-scroll.left}px, ${-scroll.top}px)` }}
        >
          {lines.map((line, i) => {
            const { head, comment } = splitStrategyConfigLineComment(line);
            return (
              <span key={i} className="block min-h-[1.25em]">
                {head || "\u00a0"}
                {comment ? <span className="text-purple-400">{comment}</span> : null}
              </span>
            );
          })}
        </code>
      </pre>
      <textarea
        ref={taRef}
        className={`relative z-10 block w-full resize-y bg-transparent ${innerPad} ${fontClass} text-transparent placeholder:text-slate-500/75 placeholder:italic caret-slate-200 outline-none ${minHeightClass}`}
        spellCheck={spellCheck}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={onScroll}
      />
    </div>
  );
}
