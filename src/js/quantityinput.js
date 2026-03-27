function mk_unit_system(system) {
    // Indirect eval to prevent access to local scope
    return new UnitSystem((0, eval)(props.system))
}

export default (props) => {
    let system;
    if (typeof props.system === 'string') {
        system = mk_unit_system(props.system);
    } else {
        system = new UnitSystem(props.system);
    }
    console.log(system)
    return {
        viewUnit: props.unit || props.canonicalUnit || system.firstUnit(),
        inputUnit: props.magnitude !== undefined ? props.unit : undefined,
        viewValue: props.magnitude,
        inputValue: props.magnitude,

        unitChange(unit) {
            console.log('unitChange', unit, system)
            this.viewUnit = unit
            if (this.inputValue !== undefined && this.inputUnit !== undefined) {
                let newVal = system.convert(this.inputUnit, this.viewUnit, this.inputValue);
                this.viewValue = newVal.toPrecision(5).replace(/\.?0+$/, '')
            }
            this.$root.dispatchEvent(new CustomEvent('view-change', {
                bubbles: true,
                detail: [this.viewValue, this.viewUnit]
            }));
        },

        input($event) {
            const value = parseFloat($event.currentTarget.value)
            this.inputValue = value
            this.inputUnit = this.viewUnit
            this.viewValue = this.inputValue

            if (props.canonicalUnit !== undefined) {
                const canonicalValue = system.convert(this.inputUnit, props.canonicalUnit, this.inputValue)
                if (canonicalValue !== undefined) {
                    this.$dispatch('input-canonical', [canonicalValue, props.canonicalUnit])
                }
            }

            this.$root.dispatchEvent(new CustomEvent('input', {
                bubbles: true,
                detail: [this.inputValue, this.inputUnit]
            }));
        }
    };
};
