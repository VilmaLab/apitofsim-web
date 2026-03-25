import AlpineModule from 'alpinejs'
import HtmxModule from 'htmx.org'
import 'htmx-ext-sse'
import $3DmolModule from '3dmol';

declare global {
  var Alpine: typeof AlpineModule;
  var htmx: typeof HtmxModule;
  var $3Dmol: typeof $3DmolModule;
}

window.Alpine = AlpineModule;
AlpineModule.start();

window.htmx = HtmxModule;
