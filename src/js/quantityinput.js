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
    const viewUnit = props.viewUnit || props.unit || props.canonicalUnit || system.firstUnit();
    const convertForDisplay = (unit, value, target) =>
        Number(system.convert(unit, target, value).toPrecision(5));
    return {
        viewUnit,
        inputUnit: props.magnitude !== undefined ? props.unit : undefined,
        viewValue: props.magnitude !== undefined && viewUnit !== props.unit
            ? convertForDisplay(props.unit, props.magnitude, viewUnit) : props.magnitude,
        inputValue: props.magnitude,

        reset([magnitude, unit]) {
            this.inputValue = magnitude;
            this.inputUnit = unit;
            this.viewValue = system.convert(unit, this.viewUnit, magnitude);
            this.$refs.quantityValue.value = this.viewValue;
            this.$dispatch('input');
        },

        unitChange(unit) {
            console.log('unitChange', unit, system)
            this.viewUnit = unit
            if (this.inputValue !== undefined && this.inputUnit !== undefined) {
                this.viewValue = convertForDisplay(this.inputUnit, this.inputValue, this.viewUnit);
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
