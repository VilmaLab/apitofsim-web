export default ({ unit, dropdown, system }) => {
    return {
        unit,
        dropdown,
        system,
        viewUnit: unit,

        setUnit(value) {
            this.viewUnit = value
            this.$dispatch('unit-change', value)
        },

        cycle() {
            this.setUnit(this.system.nextUnit(this.viewUnit))
        }
    }
};
