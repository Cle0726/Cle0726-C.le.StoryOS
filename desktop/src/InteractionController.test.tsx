/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import InteractionController from './InteractionController';

let controllerRoot: Root | null = null;

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function installWorkspace({ manuscript = true }: { manuscript?: boolean } = {}) {
  document.body.innerHTML = `
    <div id="root">
      <main class="product-shell">
        <header class="product-topbar">
          <div class="product-top-actions">
            <button>专注模式</button>
            <button>刷新项目</button>
            <button>切换作品</button>
          </div>
        </header>
        <section class="product-workspace-grid">
          <aside class="product-navigator">
            <div class="navigator-heading"><button class="icon-action">＋</button></div>
            <div class="product-nav-tabs"><button>正文</button><button>人物/实体</button><button>治理</button></div>
            <div class="product-nav-list"><button class="nav-item"><span class="nav-index">EP01</span><span><strong>第一章</strong><small>100 字符</small></span></button></div>
          </aside>
          <section class="product-canvas">
            ${manuscript ? '<div class="product-editor-heading"><span class="draft-state">已保存</span><button class="save-button">保存正文</button></div><textarea class="manuscript-editor">alpha beta alpha</textarea>' : ''}
          </section>
          <aside class="product-inspector"></aside>
        </section>
      </main>
    </div>
    <div id="controller-root"></div>
  `;

  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
  window.requestAnimationFrame = ((callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  }) as typeof window.requestAnimationFrame;

  controllerRoot = createRoot(document.getElementById('controller-root')!);
  act(() => controllerRoot!.render(<InteractionController />));
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.className = '';
  document.documentElement.removeAttribute('style');
});

afterEach(() => {
  if (controllerRoot) {
    act(() => controllerRoot?.unmount());
    controllerRoot = null;
  }
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('InteractionController', () => {
  it('opens the command palette with Cmd/Ctrl+K', () => {
    installWorkspace();

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
    });

    expect(document.querySelector('[aria-label="StoryOS 命令面板"]')).not.toBeNull();
  });

  it('opens manuscript search with Cmd/Ctrl+F', () => {
    installWorkspace();

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, bubbles: true }));
    });

    expect(document.querySelector('[aria-label="查找当前正文"]')).not.toBeNull();
  });

  it('does not steal Cmd/Ctrl+Shift+F from the existing focus-mode shortcut', () => {
    installWorkspace();
    const downstream = vi.fn();
    window.addEventListener('keydown', downstream);

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'f',
        ctrlKey: true,
        shiftKey: true,
        bubbles: true,
      }));
    });

    expect(document.querySelector('[aria-label="查找当前正文"]')).toBeNull();
    expect(downstream).toHaveBeenCalledTimes(1);
    window.removeEventListener('keydown', downstream);
  });

  it('persists keyboard resizing as a local UI preference', () => {
    installWorkspace();
    const resizer = document.querySelector<HTMLButtonElement>('.nav-resizer');
    expect(resizer).not.toBeNull();

    act(() => {
      resizer!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });

    expect(Number(window.localStorage.getItem('cle.storyos.ui.nav-width.v1'))).toBeGreaterThan(244);
    expect(document.documentElement.style.getPropertyValue('--storyos-nav-width')).toMatch(/px$/);
  });

  it('keeps manuscript search disabled when no manuscript is open', () => {
    installWorkspace({ manuscript: false });
    const searchButton = document.querySelector<HTMLButtonElement>('.interaction-dock button[aria-label="查找当前正文"]');
    expect(searchButton?.disabled).toBe(true);
  });
});
