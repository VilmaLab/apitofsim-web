export default (errors) => ({
    current_tab: window.location.hash.replace('#', '') || 'about',
    uneditableErrors: [],

    async init() {
        await this.$nextTick();
        for (const error of errors) {
            const control = Array.from(this.$el.elements).find(control =>
                (control.id === error.id || control.name === error.name)
                && control.willValidate && control.type !== 'range');
            if (control) {
                control.setCustomValidity(error.message);
                control.setAttribute('aria-invalid', 'true');
            } else {
                this.uneditableErrors.push(error);
            }
        }
        if (errors.length) this.showErrors(true);
    },

    clearError(event) {
        // Quantity/unit controls and paired voltage inputs share their wrapper.
        const target = event.target;
        const quantity = target.closest('[x-data^="quantityinput"]');
        const controls = quantity ? quantity.querySelectorAll('input')
            : target.type === 'range' ? [target, target.nextElementSibling] : [target];
        for (const control of controls) {
            if (typeof control?.setCustomValidity === 'function') {
                control.setCustomValidity('');
                control.removeAttribute('aria-invalid');
            }
        }
    },

    showErrors(includeUneditable = false) {
        const invalid = Array.from(this.$el.elements).filter(control =>
            control.willValidate && !control.validity.valid);
        const panels = Array.from(this.$el.querySelectorAll('.tab-page > div > [role="tabpanel"]'));
        const panel = panels.find(panel => invalid.some(control => panel.contains(control))
            || (includeUneditable && this.uneditableErrors.some(error => error.tab === panel.getAttribute('name'))));
        if (!panel) return invalid.length > 0;
        const name = panel.getAttribute('name');
        this.current_tab = name;
        this.$nextTick().then(() => {
            const control = invalid.find(control => panel.contains(control));
            if (control) {
                control.focus();
                control.reportValidity();
            } else {
                this.$el.querySelector(`a[href="#${name}"]`).focus();
            }
        });
        return true;
    },

    submit(event) {
        if (this.showErrors()) event.preventDefault();
    },
});
