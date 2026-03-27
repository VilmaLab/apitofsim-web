type IntoConversions = { [key: string]: ((input: number) => number) };

interface Unit {
  name: string
  conversions: IntoConversions
}

export class UnitSystem {
  unitIndex: { [key: string]: number };
  units: Unit[];

  constructor(units: Unit[]) {
    this.unitIndex = {};
    this.units = [];

    // (1)
    if (units.length < 1) {
      throw new Error("At least one unit must be provided");
    }

    for (let [idx, unit] of units.entries()) {
      this.unitIndex[unit.name] = idx;
      this.units.push(unit);
    }
  }

  firstUnit(): string {
    return this.units[0]!.name; // By (1)
  }

  nextUnit(current: string): string {
    const idx = this.unitIndex[current];
    if (idx === undefined) {
      throw new Error(`Unknown unit ${current}`);
    }
    const nextIdx = (idx + 1) % this.units.length; // (2)
    return this.units[nextIdx].name; // By (1) & (2)
  }

  getUnit(unitName: string): Unit {
    console.log(unitName);
    console.log(this.unitIndex);
    console.log(this.units);
    let unit = this.units?.[this.unitIndex?.[unitName]];
    if (unit === undefined) {
      throw new Error(`Unknown unit ${unit}`);
    }
    return unit;
  }

  convert(fromUnit: string, toUnit: string, value: number): number {
    if (fromUnit === toUnit) {
      return value;
    }
    let toUnitObj = this.units?.[this.unitIndex?.[toUnit]];
    let converter = toUnitObj?.conversions?.[fromUnit];
    if (converter === undefined) {
      throw new Error(`No conversion from ${fromUnit} to ${toUnit}`);
    }
    return converter(value);
  }
}
