// Some filters use a list of IDs as the value of a single option. Compare those
// by contents because option lists are often rebuilt during Svelte updates.
export function sameSelectValue(left, right) {
    if (Array.isArray(left) && Array.isArray(right)) {
        return left.length === right.length && left.every((value, index) => value === right[index]);
    }
    return left === right;
}
