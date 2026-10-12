import { LitElement, css, html, nothing, svg } from 'lit';
import { customElement, property, query, state } from 'lit/decorators.js';

interface ViewBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

@customElement('svg-focus')
export class SvgFocus extends LitElement {
  /**
   * Fragment reference to the master unlabelled diagram.  It, the region and
   * the labels must all live in the same SVG, which is copied into the shadow
   * root so that the fragment references resolve.
   *
   * Example: "#diagram"
   */
  @property()
  accessor diagram = '';

  /**
   * Fragment reference to the focus region.
   * Used both for cropping and for the lightbox mask.
   *
   * Example: "#region-circle"
   */
  @property()
  accessor region = '';

  /**
   * Optional fragment reference to the annotation layer.
   *
   * Example: "#labels-circle"
   */
  @property()
  accessor labels: string | undefined = undefined;

  /**
   * Full viewBox of the master diagram. Defaults to the viewBox of the SVG
   * that defines it.
   */
  @property({ attribute: 'view-box' })
  accessor viewBox = '';

  /**
   * Accessible/title text.
   */
  @property()
  accessor title = 'Diagram detail';

  /**
   * Extra SVG-coordinate padding around the region crop.
   */
  @property({ type: Number })
  accessor padding = 20;

  /**
   * Opacity of the dimmed area in the lightbox.
   */
  @property({ type: Number, attribute: 'dim-opacity' })
  accessor dimOpacity = 0.65;

  @state()
  private accessor crop: ViewBox | undefined = undefined;

  @query('dialog')
  private accessor dialog!: HTMLDialogElement;

  private readonly maskId =
    `focus-mask-${crypto.randomUUID()}`;

  static styles = css`
    :host {
      display: inline-block;
    }

    .sprite {
      position: absolute;
      width: 0;
      height: 0;
      overflow: hidden;
    }

    button.thumbnail {
      display: block;
      width: 100%;
      padding: 0;
      border: 0;
      background: none;
      color: inherit;
      cursor: pointer;
    }

    .thumbnail svg {
      display: block;
      width: 100%;
      height: auto;

      /* Set from the focus region; 4 / 3 is only the pre-measurement fallback. */
      aspect-ratio: var(--svg-focus-thumbnail-aspect, 4 / 3);
      max-height: var(--svg-focus-thumbnail-max-height, 16rem);

      overflow: hidden;
    }

    dialog {
      width: min(
        var(--svg-focus-dialog-width, 90vw),
        var(--svg-focus-dialog-max-width, 72rem)
      );

      max-width: none;
      padding: 0;
      border: 0;
      background: transparent;
    }

    dialog::backdrop {
      background: rgb(0 0 0 / 0.7);
    }

    .dialog-frame {
      position: relative;

      padding: var(--svg-focus-dialog-padding, 1rem);

      border-radius:
        var(--svg-focus-dialog-radius, 0.5rem);

      background:
        var(--svg-focus-dialog-background, white);
    }

    .dialog-svg {
      display: block;
      width: 100%;
      height: auto;
      max-height: 85vh;
    }

    .close {
      position: absolute;
      z-index: 1;
      top: 0.5rem;
      right: 0.5rem;

      width: 2.5rem;
      height: 2.5rem;
      padding: 0;

      border: 0;
      border-radius: 999px;

      background:
        var(--svg-focus-close-background, white);

      color:
        var(--svg-focus-close-color, black);

      font: inherit;
      font-size: 1.5rem;
      line-height: 1;

      cursor: pointer;
    }
  `;

  connectedCallback(): void {
    super.connectedCallback();

    window.addEventListener(
      'resize',
      this.handleResize,
    );
  }

  disconnectedCallback(): void {
    window.removeEventListener(
      'resize',
      this.handleResize,
    );

    super.disconnectedCallback();
  }

  protected firstUpdated(): void {
    this.cloneSprite();
    this.calculateCrop();
  }

  protected updated(
    changed: Map<PropertyKey, unknown>,
  ): void {
    if (
      changed.has('region') ||
      changed.has('padding')
    ) {
      this.calculateCrop();
    }
  }

  public refresh(): void {
    this.calculateCrop();
  }

  public open(): void {
    if (!this.dialog.open) {
      this.dialog.showModal();
    }
  }

  public close(): void {
    if (this.dialog.open) {
      this.dialog.close();
    }
  }

  private handleResize = (): void => {
    this.calculateCrop();
  };

  /**
   * Copy the shared sprite into this shadow root.  Fragment references do not
   * cross shadow DOM boundaries, so without this the <use> elements in the
   * thumbnail and the lightbox would resolve to nothing.
   */
  private cloneSprite(): void {
    const container =
      this.renderRoot.querySelector('.sprite');

    const diagram = this.diagram
      ? document.querySelector(this.diagram)
      : null;

    const sprite = diagram?.closest('svg');

    if (!container || !sprite) {
      console.warn(
        `<svg-focus>: master diagram "${this.diagram}" was not found.`,
      );
      return;
    }

    container.replaceChildren(sprite.cloneNode(true));
  }

  private calculateCrop(): void {
    if (!this.region) {
      return;
    }

    requestAnimationFrame(() => {
      const region =
        document.querySelector<SVGGraphicsElement>(
          this.region,
        );

      if (!region) {
        console.warn(
          `<svg-focus>: region "${this.region}" was not found.`,
        );
        return;
      }

      const box = region.getBBox();

      this.crop = {
        x: box.x - this.padding,
        y: box.y - this.padding,
        width: box.width + this.padding * 2,
        height: box.height + this.padding * 2,
      };

      if (this.crop.height) {
        this.style.setProperty(
          '--svg-focus-thumbnail-aspect',
          `${this.crop.width} / ${this.crop.height}`,
        );
      }
    });
  }

  /**
   * The viewBox of the whole diagram: the explicit one when given, otherwise
   * the one declared by the SVG that owns the referenced master diagram.
   */
  private resolveViewBox(): string {
    if (this.viewBox || !this.diagram) {
      return this.viewBox || '0 0 600 400';
    }

    const diagram = this.renderRoot.querySelector(this.diagram)
      ?? document.querySelector(this.diagram);

    return diagram?.closest('svg')?.getAttribute('viewBox')
      ?? '0 0 600 400';
  }

  private formatViewBox(box: ViewBox): string {
    return [
      box.x,
      box.y,
      box.width,
      box.height,
    ].join(' ');
  }

  /**
   * The part of the diagram the thumbnail shows.  Until the region has been
   * measured that is the whole diagram.
   */
  private thumbnailViewBox(): ViewBox {
    if (this.crop) {
      return this.crop;
    }

    const [x, y, width, height] = this.resolveViewBox()
      .split(/[\s,]+/)
      .map(Number);

    return { x, y, width, height };
  }

  /**
   * A dimmer that is visible everywhere except inside the focus region, so the
   * region reads as the lit part of the picture.  It covers more than the
   * viewBox on purpose: a thumbnail whose aspect ratio does not match the crop
   * shows diagram content outside the viewBox, and that has to be dimmed too.
   *
   * A <mask> is never rendered directly, so both elements can sit inline.
   */
  private dimmer(id: string) {
    return svg`
      <mask id=${id}>
        <rect
          x="-10000"
          y="-10000"
          width="20000"
          height="20000"
          fill="white"
        ></rect>

        <use
          href=${this.region}
          fill="black"
          stroke="none"
        ></use>
      </mask>

      <rect
        x="-10000"
        y="-10000"
        width="20000"
        height="20000"
        fill="black"
        opacity=${this.dimOpacity}
        mask=${`url(#${id})`}
      ></rect>
    `;
  }

  /**
   * Annotation layer, drawn above the dimmer so it stays legible.  A nested
   * html template would be parsed outside the SVG namespace, so keep it SVG.
   */
  private labelsLayer() {
    return this.labels
      ? svg`
          <use
            href=${this.labels}
          ></use>
        `
      : nothing;
  }

  private handleDialogClick(
    event: MouseEvent,
  ): void {
    if (event.target === this.dialog) {
      this.close();
    }
  }

  render() {
    const thumbnail = this.thumbnailViewBox();
    const thumbnailMask = `${this.maskId}-thumbnail`;

    return html`
      <div class="sprite" aria-hidden="true"></div>

      <button
        type="button"
        class="thumbnail"
        aria-label=${this.title}
        @click=${this.open}
      >
        <svg
          viewBox=${this.formatViewBox(thumbnail)}
          role="img"
          aria-label=${this.title}
          preserveAspectRatio="xMidYMid meet"
        >
          <!-- Master diagram, labels, and the dimmer in between -->
          <use href=${this.diagram}></use>

          ${this.dimmer(thumbnailMask)}

          ${this.labelsLayer()}
        </svg>
      </button>

      <dialog
        aria-label=${this.title}
        @click=${this.handleDialogClick}
      >
        <div class="dialog-frame">
          <button
            type="button"
            class="close"
            aria-label="Close"
            @click=${this.close}
          >
            &times;
          </button>

          <svg
            class="dialog-svg"
            viewBox=${this.resolveViewBox()}
            role="img"
            aria-label=${this.title}
            preserveAspectRatio="xMidYMid meet"
          >
            <!-- Master diagram, labels, and the dimmer in between -->
            <use href=${this.diagram}></use>

            ${this.dimmer(this.maskId)}

            <!-- Optional focus outline -->
            <use
              href=${this.region}
              fill="none"
              stroke="var(--svg-focus-outline, white)"
              stroke-width="var(--svg-focus-outline-width, 3)"
              vector-effect="non-scaling-stroke"
            ></use>

            ${this.labelsLayer()}
          </svg>
        </div>
      </dialog>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'svg-focus': SvgFocus;
  }
}
