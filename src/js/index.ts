import AlpineModule from 'alpinejs'
import HtmxModule from 'htmx.org'
import 'htmx-ext-sse'
import $3DmolModule from '3dmol';
import {UnitSystem as UnitSystemCls} from './unitsystem.ts';
import quantityinput from './quantityinput.js';
import unitswitcher from './unitswitcher.js';

declare global {
  var Alpine: typeof AlpineModule;
  var htmx: typeof HtmxModule;
  var $3Dmol: typeof $3DmolModule;
  var UnitSystem: typeof UnitSystemCls;
}

window.htmx = HtmxModule;
window.UnitSystem = UnitSystemCls;

AlpineModule.data('quantityinput', quantityinput);
AlpineModule.data('unitswitcher', unitswitcher);

window.Alpine = AlpineModule;
AlpineModule.start();
