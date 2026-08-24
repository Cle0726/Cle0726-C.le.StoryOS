import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';

const NAV_COLLAPSED_KEY = 'cle.storyos.ui.nav-collapsed.v1';
const INSPECTOR_COLLAPSED_KEY = 'cle.storyos.ui.inspector-collapsed.v1';
const NAV_WIDTH_KEY = 'cle.storyos.ui.nav-width.v1';
const INSPECTOR_WIDTH_KEY = 'cle.storyos.ui.inspector-width.v1';

interface PaletteCommand {
  id: string;
  label: string;
  detail?: string;
  keywords?: string;
  run: () => void;
}

interface SearchMatch {
  start: number;
  end: number;
  snippet: string;
}

type ResizeSide = 'nav' | 'inspector';

function storedBoolean(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

function storedNumber(key: string, fallback: number): number {
  try {
    const parsed = Number(window.localStorage.getItem(key));
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function setRootClass(name: string, enabled: boolean) {
  document.documentElement.classList.toggle(name, enabled);
}

function setRootStyle(name: string, value: string) {
  document.documentElement.style.setProperty(name, value);
}

function syncWorkspaceGeometry() {
  const grid = document.querySelector<HTMLElement>('.product-workspace-grid');
  const nav = document.querySelector<HTMLElement>('.product-navigator');
  const inspector = document.querySelector<HTMLElement>('.product-inspector');
  if (grid) setRootStyle('--storyos-workspace-top', `${Math.round(grid.getBoundingClientRect().top)}px`);
  if (nav) setRootStyle('--storyos-nav-edge', `${Math.round(nav.getBoundingClientRect().right)}px`);
  if (inspector) setRootStyle('--storyos-inspector-edge', `${Math.round(inspector.getBoundingClientRect().left)}px`);
}

function clampPanelWidth(side: ResizeSide, value: number): number {
  if (side === 'nav') {
    const max = Math.max(220, Math.min(380, window.innerWidth - 650));
    return Math.round(Math.max(190, Math.min(max, value)));
  }
  const max = Math.max(310, Math.min(460, window.innerWidth - 760));
  return Math.round(Math.max(270, Math.min(max, value)));
}

function findButtonByText(text: string): HTMLButtonElement | null {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
    .find((button) => button.textContent?.trim() === text) ?? null;
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

function CommandIcon() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2.5" y="3.25" width="13" height="11.5" rx="2" />
      <path d="M5.25 7.25h7.5M5.25 10.75h4.5" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <circle cx="8" cy="8" r="4.35" />
      <path d="m11.25 11.25 3.15 3.15" />
    </svg>
  );
}

export default function InteractionController() {
  const [navCollapsed, setNavCollapsed] = useState(() => storedBoolean(NAV_COLLAPSED_KEY));
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => storedBoolean(INSPECTOR_COLLAPSED_KEY));
  const [workspaceVisible, setWorkspaceVisible] = useState(() => !!document.querySelector('.product-shell'));
  const [focusMode, setFocusMode] = useState(() => !!document.querySelector('.product-shell.focus-mode'));
  const [toast, setToast] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState('');
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [commands, setCommands] = useState<PaletteCommand[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatches, setSearchMatches] = useState<SearchMatch[]>([]);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const [resizingSide, setResizingSide] = useState<ResizeSide | null>(null);

  const toastTimerRef = useRef<number | null>(null);
  const navWidthRef = useRef(storedNumber(NAV_WIDTH_KEY, 244));
  const inspectorWidthRef = useRef(storedNumber(INSPECTOR_WIDTH_KEY, 316));
  const resizeRef = useRef<{ side: ResizeSide; startX: number; startWidth: number } | null>(null);
  const paletteInputRef = useRef<HTMLInputElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const lastInlineStatusRef = useRef('');
  const lastErrorRef = useRef('');

  const announce = useCallback((message: string) => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    setToast(message);
    toastTimerRef.current = window.setTimeout(() => {
      toastTimerRef.current = null;
      setToast(null);
    }, 1900);
  }, []);

  useEffect(() => {
    navWidthRef.current = clampPanelWidth('nav', navWidthRef.current);
    inspectorWidthRef.current = clampPanelWidth('inspector', inspectorWidthRef.current);
    setRootStyle('--storyos-nav-width', `${navWidthRef.current}px`);
    setRootStyle('--storyos-inspector-width', `${inspectorWidthRef.current}px`);
  }, []);

  useEffect(() => {
    setRootClass('storyos-nav-collapsed', navCollapsed);
    try { window.localStorage.setItem(NAV_COLLAPSED_KEY, navCollapsed ? '1' : '0'); } catch { /* local preference only */ }
    window.requestAnimationFrame(syncWorkspaceGeometry);
  }, [navCollapsed]);

  useEffect(() => {
    setRootClass('storyos-inspector-collapsed', inspectorCollapsed);
    try { window.localStorage.setItem(INSPECTOR_COLLAPSED_KEY, inspectorCollapsed ? '1' : '0'); } catch { /* local preference only */ }
    window.requestAnimationFrame(syncWorkspaceGeometry);
  }, [inspectorCollapsed]);

  useEffect(() => {
    setRootClass('storyos-focus-active', focusMode);
  }, [focusMode]);

  useEffect(() => {
    setRootClass('storyos-resizing', resizingSide !== null);
    return () => setRootClass('storyos-resizing', false);
  }, [resizingSide]);

  useEffect(() => {
    const root = document.getElementById('root');
    if (!root) return undefined;

    const sync = () => {
      const shell = root.querySelector('.product-shell');
      const activeFocus = !!root.querySelector('.product-shell.focus-mode');
      setWorkspaceVisible(!!shell);
      setFocusMode(activeFocus);
      syncWorkspaceGeometry();

      const inlineStatus = root.querySelector('.product-editor-heading .draft-state')?.textContent?.trim() ?? '';
      if (inlineStatus && lastInlineStatusRef.current && inlineStatus !== lastInlineStatusRef.current) {
        if (inlineStatus.includes('正式保存') || inlineStatus === '已保存') announce('正文已正式保存');
      }
      lastInlineStatusRef.current = inlineStatus;

      const currentError = root.querySelector('.global-banner.error-banner')?.textContent?.trim() ?? '';
      if (currentError && currentError !== lastErrorRef.current) announce('操作失败，请查看顶部提示');
      lastErrorRef.current = currentError;
    };

    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', sync);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', sync);
    };
  }, [announce]);

  useEffect(() => () => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
  }, []);

  useEffect(() => {
    if (!resizingSide) return undefined;

    const move = (event: PointerEvent) => {
      const state = resizeRef.current;
      if (!state) return;
      const delta = event.clientX - state.startX;
      const next = clampPanelWidth(
        state.side,
        state.side === 'nav' ? state.startWidth + delta : state.startWidth - delta,
      );
      if (state.side === 'nav') {
        navWidthRef.current = next;
        setRootStyle('--storyos-nav-width', `${next}px`);
      } else {
        inspectorWidthRef.current = next;
        setRootStyle('--storyos-inspector-width', `${next}px`);
      }
      window.requestAnimationFrame(syncWorkspaceGeometry);
    };

    const finish = () => {
      const state = resizeRef.current;
      if (state) {
        try {
          window.localStorage.setItem(
            state.side === 'nav' ? NAV_WIDTH_KEY : INSPECTOR_WIDTH_KEY,
            String(state.side === 'nav' ? navWidthRef.current : inspectorWidthRef.current),
          );
        } catch { /* local preference only */ }
      }
      resizeRef.current = null;
      setResizingSide(null);
      announce('工作区宽度已保存');
      window.requestAnimationFrame(syncWorkspaceGeometry);
    };

    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish, { once: true });
    window.addEventListener('pointercancel', finish, { once: true });
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
    };
  }, [announce, resizingSide]);

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

  function beginResize(side: ResizeSide, event: ReactPointerEvent<HTMLButtonElement>) {
    if (focusMode) return;
    const panel = document.querySelector<HTMLElement>(side === 'nav' ? '.product-navigator' : '.product-inspector');
    if (!panel) return;
    resizeRef.current = {
      side,
      startX: event.clientX,
      startWidth: panel.getBoundingClientRect().width,
    };
    setResizingSide(side);
    event.preventDefault();
  }

  function keyboardResize(side: ResizeSide, event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const logicalDirection = side === 'nav' ? direction : -direction;
    const current = side === 'nav' ? navWidthRef.current : inspectorWidthRef.current;
    const next = clampPanelWidth(side, current + logicalDirection * 12);
    if (side === 'nav') {
      navWidthRef.current = next;
      setRootStyle('--storyos-nav-width', `${next}px`);
    } else {
      inspectorWidthRef.current = next;
      setRootStyle('--storyos-inspector-width', `${next}px`);
    }
    try { window.localStorage.setItem(side === 'nav' ? NAV_WIDTH_KEY : INSPECTOR_WIDTH_KEY, String(next)); } catch { /* local preference only */ }
    window.requestAnimationFrame(syncWorkspaceGeometry);
  }

  function resetPanelWidth(side: ResizeSide) {
    const next = side === 'nav' ? 244 : 316;
    if (side === 'nav') {
      navWidthRef.current = next;
      setRootStyle('--storyos-nav-width', `${next}px`);
    } else {
      inspectorWidthRef.current = next;
      setRootStyle('--storyos-inspector-width', `${next}px`);
    }
    try { window.localStorage.removeItem(side === 'nav' ? NAV_WIDTH_KEY : INSPECTOR_WIDTH_KEY); } catch { /* local preference only */ }
    announce(side === 'nav' ? '作品书架宽度已重置' : '上下文宽度已重置');
    window.requestAnimationFrame(syncWorkspaceGeometry);
  }

  function openSearch() {
    if (!document.querySelector<HTMLTextAreaElement>('.manuscript-editor')) {
      announce('请先打开一个正文章节');
      return;
    }
    setPaletteOpen(false);
    setSearchQuery('');
    setSearchMatches([]);
    setSearchTruncated(false);
    setSearchOpen(true);
    window.setTimeout(() => searchInputRef.current?.focus(), 0);
  }

  const buildCommands = useCallback((): PaletteCommand[] => {
    const rows: PaletteCommand[] = [
      { id: 'view-nav', label: navCollapsed ? '展开作品书架' : '收起作品书架', detail: '视图', keywords: '导航 左侧 书架', run: toggleNav },
      { id: 'view-inspector', label: inspectorCollapsed ? '展开上下文' : '收起上下文', detail: '视图', keywords: '右侧 上下文 检查器', run: toggleInspector },
      { id: 'view-focus', label: focusMode ? '退出专注模式' : '进入专注模式', detail: 'Cmd/Ctrl + Shift + F', keywords: 'focus 专注 写作', run: toggleFocus },
    ];

    const editor = document.querySelector<HTMLTextAreaElement>('.manuscript-editor');
    if (editor) {
      rows.push({ id: 'find-manuscript', label: '查找当前正文', detail: 'Cmd/Ctrl + F', keywords: '全文 搜索 查找', run: openSearch });
      const saveButton = document.querySelector<HTMLButtonElement>('.save-button');
      if (saveButton && !saveButton.disabled) {
        rows.push({
          id: 'save-manuscript',
          label: '保存当前正文',
          detail: 'Cmd/Ctrl + S',
          keywords: 'save 保存 正文',
          run: () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', code: 'KeyS', ctrlKey: true, metaKey: true, bubbles: true })),
        });
      }
    }

    const newChapter = document.querySelector<HTMLButtonElement>('.navigator-heading .icon-action');
    if (newChapter && !newChapter.disabled) rows.push({ id: 'new-chapter', label: '新建章节', detail: '作品', keywords: 'chapter 章节 新建', run: () => newChapter.click() });

    for (const label of ['正文', '人物/实体', '治理']) {
      const button = Array.from(document.querySelectorAll<HTMLButtonElement>('.product-nav-tabs button'))
        .find((item) => item.textContent?.trim() === label);
      if (button) rows.push({ id: `tab-${label}`, label: `打开${label}`, detail: '工作区', keywords: `${label} 导航`, run: () => button.click() });
    }

    const refresh = findButtonByText('刷新项目');
    if (refresh && !refresh.disabled) rows.push({ id: 'refresh', label: '刷新项目', detail: '项目', keywords: 'reload refresh 刷新', run: () => refresh.click() });
    const switchProject = findButtonByText('切换作品');
    if (switchProject && !switchProject.disabled) rows.push({ id: 'switch-project', label: '切换作品', detail: '项目', keywords: 'project launcher 作品 项目', run: () => switchProject.click() });

    Array.from(document.querySelectorAll<HTMLButtonElement>('.product-nav-list .nav-item')).forEach((button, index) => {
      const title = button.querySelector('strong')?.textContent?.trim() || button.textContent?.trim() || `项目 ${index + 1}`;
      const detail = [button.querySelector('.nav-index')?.textContent?.trim(), button.querySelector('small')?.textContent?.trim()].filter(Boolean).join(' · ');
      rows.push({
        id: `nav-${index}-${title}`,
        label: `打开：${title}`,
        detail: detail || '当前列表',
        keywords: button.textContent?.trim() ?? title,
        run: () => button.click(),
      });
    });

    return rows;
  }, [focusMode, inspectorCollapsed, navCollapsed]);

  function openPalette() {
    setCommands(buildCommands());
    setPaletteQuery('');
    setPaletteIndex(0);
    setSearchOpen(false);
    setPaletteOpen(true);
    window.setTimeout(() => paletteInputRef.current?.focus(), 0);
  }

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const key = event.key.toLocaleLowerCase();
      if ((event.metaKey || event.ctrlKey) && key === 'k') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (paletteOpen) setPaletteOpen(false);
        else openPalette();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && key === 'f' && document.querySelector('.manuscript-editor')) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (searchOpen) setSearchOpen(false);
        else openSearch();
        return;
      }
      if (event.key === 'Escape' && (paletteOpen || searchOpen)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setPaletteOpen(false);
        setSearchOpen(false);
      }
    };
    window.addEventListener('keydown', handleShortcut, true);
    return () => window.removeEventListener('keydown', handleShortcut, true);
  }, [paletteOpen, searchOpen, buildCommands]);

  useEffect(() => {
    if (!searchOpen || !searchQuery) {
      setSearchMatches([]);
      setSearchTruncated(false);
      return;
    }
    const editor = document.querySelector<HTMLTextAreaElement>('.manuscript-editor');
    if (!editor) return;
    const source = editor.value;
    const haystack = source.toLocaleLowerCase();
    const needle = searchQuery.toLocaleLowerCase();
    const matches: SearchMatch[] = [];
    let cursor = 0;
    let truncated = false;
    while (cursor <= haystack.length - needle.length) {
      const index = haystack.indexOf(needle, cursor);
      if (index < 0) break;
      if (matches.length >= 200) {
        truncated = true;
        break;
      }
      const before = Math.max(0, index - 34);
      const after = Math.min(source.length, index + needle.length + 54);
      const snippet = source.slice(before, after).replace(/\s+/g, ' ').trim();
      matches.push({ start: index, end: index + needle.length, snippet });
      cursor = index + Math.max(1, needle.length);
    }
    setSearchMatches(matches);
    setSearchTruncated(truncated);
  }, [searchOpen, searchQuery]);

  function runCommand(command: PaletteCommand) {
    setPaletteOpen(false);
    command.run();
  }

  function locateSearchMatch(match: SearchMatch) {
    const editor = document.querySelector<HTMLTextAreaElement>('.manuscript-editor');
    if (!editor) return;
    setSearchOpen(false);
    window.setTimeout(() => {
      editor.focus();
      editor.setSelectionRange(match.start, match.end);
    }, 0);
  }

  const filteredCommands = useMemo(() => {
    const query = paletteQuery.trim().toLocaleLowerCase();
    if (!query) return commands;
    return commands.filter((command) => `${command.label} ${command.detail ?? ''} ${command.keywords ?? ''}`.toLocaleLowerCase().includes(query));
  }, [commands, paletteQuery]);

  useEffect(() => {
    if (paletteIndex >= filteredCommands.length) setPaletteIndex(0);
  }, [filteredCommands.length, paletteIndex]);

  if (!workspaceVisible) return null;

  return (
    <>
      <button
        type="button"
        className="workspace-resizer nav-resizer"
        aria-label="调整作品书架宽度"
        title="拖动调整作品书架宽度；双击重置"
        disabled={focusMode || navCollapsed}
        onPointerDown={(event) => beginResize('nav', event)}
        onKeyDown={(event) => keyboardResize('nav', event)}
        onDoubleClick={() => resetPanelWidth('nav')}
      />
      <button
        type="button"
        className="workspace-resizer inspector-resizer"
        aria-label="调整上下文宽度"
        title="拖动调整上下文宽度；双击重置"
        disabled={focusMode || inspectorCollapsed}
        onPointerDown={(event) => beginResize('inspector', event)}
        onKeyDown={(event) => keyboardResize('inspector', event)}
        onDoubleClick={() => resetPanelWidth('inspector')}
      />

      <nav className="interaction-dock" aria-label="工作区显示控制">
        <button type="button" aria-label="命令面板" title="命令面板 · Cmd/Ctrl + K" onClick={openPalette}>
          <CommandIcon />
        </button>
        <button type="button" aria-label="查找当前正文" title="查找当前正文 · Cmd/Ctrl + F" onClick={openSearch} disabled={!document.querySelector('.manuscript-editor')}>
          <SearchIcon />
        </button>
        <span className="interaction-dock-divider" aria-hidden="true" />
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

      {paletteOpen && (
        <div className="command-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPaletteOpen(false); }}>
          <section className="command-palette" role="dialog" aria-modal="true" aria-label="StoryOS 命令面板">
            <div className="command-input-row">
              <CommandIcon />
              <input
                ref={paletteInputRef}
                value={paletteQuery}
                onChange={(event) => { setPaletteQuery(event.target.value); setPaletteIndex(0); }}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowDown') { event.preventDefault(); setPaletteIndex((current) => filteredCommands.length ? (current + 1) % filteredCommands.length : 0); }
                  if (event.key === 'ArrowUp') { event.preventDefault(); setPaletteIndex((current) => filteredCommands.length ? (current - 1 + filteredCommands.length) % filteredCommands.length : 0); }
                  if (event.key === 'Enter' && filteredCommands[paletteIndex]) { event.preventDefault(); runCommand(filteredCommands[paletteIndex]); }
                }}
                placeholder="输入命令、章节或人物…"
                aria-label="搜索命令"
              />
              <kbd>Esc</kbd>
            </div>
            <div className="command-results" role="listbox">
              {filteredCommands.slice(0, 18).map((command, index) => (
                <button
                  key={command.id}
                  type="button"
                  className={index === paletteIndex ? 'selected' : ''}
                  role="option"
                  aria-selected={index === paletteIndex}
                  onMouseEnter={() => setPaletteIndex(index)}
                  onClick={() => runCommand(command)}
                >
                  <span>{command.label}</span>
                  {command.detail && <small>{command.detail}</small>}
                </button>
              ))}
              {!filteredCommands.length && <div className="command-empty">没有匹配命令</div>}
            </div>
            <footer><span>↑↓ 选择</span><span>Enter 执行</span><span>Cmd/Ctrl + K 打开</span></footer>
          </section>
        </div>
      )}

      {searchOpen && (
        <div className="command-backdrop search-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSearchOpen(false); }}>
          <section className="command-palette manuscript-search" role="dialog" aria-modal="true" aria-label="查找当前正文">
            <div className="command-input-row">
              <SearchIcon />
              <input
                ref={searchInputRef}
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && searchMatches[0]) { event.preventDefault(); locateSearchMatch(searchMatches[0]); }
                }}
                placeholder="在当前正文中查找…"
                aria-label="正文查找"
              />
              <span className="search-count">{searchQuery ? `${searchMatches.length}${searchTruncated ? '+' : ''} 处` : '—'}</span>
            </div>
            <div className="search-results">
              {searchMatches.slice(0, 40).map((match, index) => (
                <button key={`${match.start}-${index}`} type="button" onClick={() => locateSearchMatch(match)}>
                  <strong>{index + 1}</strong>
                  <span>{match.snippet}</span>
                </button>
              ))}
              {searchQuery && !searchMatches.length && <div className="command-empty">当前正文中没有找到“{searchQuery}”</div>}
              {!searchQuery && <div className="command-empty">输入文字后会显示匹配位置；点击结果即可跳转到正文。</div>}
            </div>
            <footer><span>最多显示 200 处匹配</span><span>Cmd/Ctrl + F 打开</span></footer>
          </section>
        </div>
      )}
    </>
  );
}
