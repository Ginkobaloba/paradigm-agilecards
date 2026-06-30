import * as Dialog from "@radix-ui/react-dialog";

interface Props {
  open: boolean;
  onClose: () => void;
}

interface Shortcut {
  keys: string;
  description: string;
}

const SHORTCUTS: Shortcut[] = [
  { keys: "Cmd/Ctrl K", description: "Open command palette" },
  { keys: "/", description: "Open command palette (when not typing)" },
  { keys: "F", description: "Focus the filter-bar search input" },
  { keys: "?", description: "Toggle this cheatsheet" },
  { keys: "Esc", description: "Close any open dialog / palette" },
  {
    keys: "drag",
    description: "Reorder cards within a column (Rank sort) or move between columns",
  },
];

export function Cheatsheet({ open, onClose }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40" />
        <Dialog.Content
          className="fixed top-[15vh] left-1/2 -translate-x-1/2 w-[min(420px,90vw)] z-50 surface flex flex-col"
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <Dialog.Title className="text-sm font-semibold text-text">
              Keyboard shortcuts
            </Dialog.Title>
            <Dialog.Close className="btn" aria-label="Close">
              Close
            </Dialog.Close>
          </div>
          <div className="p-4 flex flex-col gap-2">
            {SHORTCUTS.map((s) => (
              <div
                key={s.keys}
                className="flex items-center justify-between gap-3"
              >
                <kbd className="font-mono text-[11px] rounded border border-border bg-panel2 px-1.5 py-0.5 text-text">
                  {s.keys}
                </kbd>
                <span className="text-[12px] text-muted text-right">
                  {s.description}
                </span>
              </div>
            ))}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
