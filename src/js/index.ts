import AlpineModule from 'alpinejs'
import HtmxModule from 'htmx.org'
import 'htmx-ext-sse'
import $3DmolModule from '3dmol';
import {UnitSystem as UnitSystemCls} from './unitsystem.ts';
import quantityinput from './quantityinput.js';
import settingsform from './settingsform.js';
import unitswitcher from './unitswitcher.js';
import './svg-focus.ts';

declare global {
  var Alpine: typeof AlpineModule;
  var htmx: typeof HtmxModule;
  var $3Dmol: typeof $3DmolModule;
  var UnitSystem: typeof UnitSystemCls;
}

window.htmx = HtmxModule;
window.UnitSystem = UnitSystemCls;

AlpineModule.data('quantityinput', quantityinput);
AlpineModule.data('settingsForm', settingsform);
AlpineModule.data('unitswitcher', unitswitcher);

window.Alpine = AlpineModule;
AlpineModule.start();

function responsiveTabs(list: HTMLElement) {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return;

  let scheduled = false;
  const update = () => {
    scheduled = false;
    const items = Array.from(list.querySelectorAll<HTMLElement>(':scope > li'));
    if (!items.length) return;

    const listStyle = getComputedStyle(list);
    const gap = parseFloat(listStyle.columnGap) || 0;
    const labelGap = parseFloat(listStyle.getPropertyValue('--tab-label-gap')) || 0;
    const widths = items.map(item => {
      const pill = item.querySelector<HTMLElement>('.tab-pill')!;
      const label = pill.querySelector<HTMLElement>('.tab-label')!;
      const icon = pill.querySelector<HTMLElement>('.icon')!;
      const pillStyle = getComputedStyle(pill);
      const labelStyle = getComputedStyle(label);
      context.font = labelStyle.font;
      const text = Math.ceil(context.measureText(label.textContent?.trim() || '').width + 2);
      label.style.setProperty('--label-width', `${text}px`);
      const iconOnly = icon.getBoundingClientRect().width
        + parseFloat(pillStyle.paddingLeft) + parseFloat(pillStyle.paddingRight)
        + parseFloat(pillStyle.borderLeftWidth) + parseFloat(pillStyle.borderRightWidth);
      return { iconOnly, full: iconOnly + labelGap + text };
    });

    const spaces = gap * (items.length - 1);
    const natural = widths.reduce((sum, width) => sum + width.full, spaces);
    const icons = widths.reduce((sum, width) => sum + width.iconOnly, spaces);
    const about = list.hasAttribute('data-has-about')
      ? natural - widths[0].full + widths[0].iconOnly
      : natural;
    const active = icons + Math.max(...widths.map(width => width.full - width.iconOnly));
    const available = list.clientWidth;

    list.dataset.mode = available >= natural ? 'natural'
      : list.hasAttribute('data-has-about') && available >= about ? 'about-icon'
      : available >= active ? 'active' : 'icons';
  };

  const schedule = () => {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(update);
    }
  };
  new ResizeObserver(schedule).observe(list);
  new MutationObserver(schedule).observe(list, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['aria-selected'],
  });
  document.fonts.ready.then(schedule);
  schedule();
}

document.querySelectorAll<HTMLElement>('[data-responsive-tabs]').forEach(responsiveTabs);
