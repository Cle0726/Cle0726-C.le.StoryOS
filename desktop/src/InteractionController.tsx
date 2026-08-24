import { useEffect, useRef, useState } from 'react';

const NAV_COLLAPSED_KEY = 'cle.storyos.ui.nav-collapsed.v1';
const INSPECTOR_COLLAPSED_KEY = 'cle.storyos.ui.inspector-collapsed.v1';

function storedBoolean(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

function setRootClass(name: string, enabled: boolean) {
  document.documentElement.classList.toggle(name, enabled);
}

function PanelIcon({ side }: { side: 'left' | 'right' }) {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2.25" y="3" width="13.5" height="12" rx="2" />
      <path d={side === 'left' ? 'M6.25 3.5v11' : 'M11.75 3.5v11'} />
      <path d={side === 'left' ? 'm9.5 7-2 2 2 2' : 'm8.5 7 2 2-2 2'} />
    </svg>
  );
}

function FocusIcon() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <path d="M6 3H3v3M12 3h3v3M6 15H3v-3M12 15h3v-3" />
      <circle cx="9" cy="9" r="2.4" />
    </svg>
  );
}

export default function InteractionController() {
  const [navCollapsed, setNavCollapsed] = useState(() => storedBoolean(NAV_COLLAPSED_KEY));
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => storedBoolean(INSPECTOR_COLLAPSED_KEY));
  const [workspaceVisible, setWorkspaceVisible] = useState(() => !!document.querySelector('.product-shell'));
  const [focusMode, setFocusMode] = useState(() => !!document.querySelector('.product-shell.focus-mode'));
  const [toast, setToast] = useState<string | null>(null);
  const toastTimerRef = useRef<number | null>(null);

  function announce(message: string) {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    setToast(message);
    toastTimerRef.current = window.setTimeout(() => {
      toastTimerRef.current = null;
      setToast(null);
    }, 1800);
  }

  useEffect(() => {
    setRootClass('storyos-nav-collapsed', navCollapsed);
    try { window.localStorage.setItem(NAV_COLLAPSED_KEY, navCollapsed ? '1' : '0'); } catch { /* local preference only */ }
  }, [navCollapsed]);

  useEffect(() => {
    setRootClass('storyos-inspector-collapsed', inspectorCollapsed);
    try { window.localStorage.setItem(INSPECTOR_COLLAPSED_KEY, inspectorCollapsed ? '1' : '0'); } catch { /* local preference only */ }
  }, [inspectorCollapsed]);

  useEffect(() => {
    const root = document.getElementById('root');
    if (!root) return undefined;
    const sync = () => {
      setWorkspaceVisible(!!root.querySelector('.product-shell'));
      setFocusMode(!!root.querySelector('.product-shell.focus-mode'));
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => () => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
  }, []);

  function toggleNav() {
    setNavCollapsed((current) => {
      const next = !current;
      announce(next ? '已收起作品书架' : '已展开作品书架');
      return next;
    });
  }

  function toggleInspector() {
    setInspectorCollapsed((current) => {
      const next = !current;
      announce(next ? '已收起上下文' : '已展开上下文');
      return next;
    });
  }

  function toggleFocus() {
    window.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'f',
      code: 'KeyF',
      ctrlKey: true,
      metaKey: true,
      shiftKey: true,
      bubbles: true,
    }));
    window.setTimeout(() => {
      const active = !!document.querySelector('.product-shell.focus-mode');
      setFocusMode(active);
      announce(active ? '已进入专注模式' : '已退出专注模式');
    }, 24);
  }

  if (!workspaceVisible) return null;

  return (
    <>
      <nav className="interaction-dock" aria-label="工作区显示控制">
        <button
          type="button"
          className={navCollapsed ? 'active' : ''}
          aria-pressed={navCollapsed}
          aria-label={navCollapsed ? '展开作品书架' : '收起作品书架'}
          title={navCollapsed ? '展开作品书架' : '收起作品书架'}
          disabled={focusMode}
          onClick={toggleNav}
        >
          <PanelIcon side="left" />
        </button>
        <button
          type="button"
          className={focusMode ? 'active focus-control' : 'focus-control'}
          aria-pressed={focusMode}
          aria-label={focusMode ? '退出专注模式' : '进入专注模式'}
          title={focusMode ? '退出专注模式' : '进入专注模式'}
          onClick={toggleFocus}
        >
          <FocusIcon />
        </button>
        <button
          type="button"
          className={inspectorCollapsed ? 'active inspector-control' : 'inspector-control'}
          aria-pressed={inspectorCollapsed}
          aria-label={inspectorCollapsed ? '展开上下文' : '收起上下文'}
          title={inspectorCollapsed ? '展开上下文' : '收起上下文'}
          disabled={focusMode}
          onClick={toggleInspector}
        >
          <PanelIcon side="right" />
        </button>
      </nav>
      {toast && <div className="interaction-toast" role="status" aria-live="polite">{toast}</div>}
    </>
  );
}
