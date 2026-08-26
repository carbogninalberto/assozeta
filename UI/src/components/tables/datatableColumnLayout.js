const HEADER_CHARACTER_WIDTH = 8;
const HEADER_HORIZONTAL_SPACE = 40;
const MINIMUM_HEADER_WIDTH = 80;
const MAXIMUM_HEADER_WIDTH = 360;

function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

function normalizeHeader(title) {
    return String(title || '')
        .replace(/<[^>]*>/g, '')
        .replace(/\s+/g, ' ')
        .trim();
}

export function estimateHeaderMinimumWidth(title, {selector = false, action = false} = {}) {
    if (selector) return 40;
    if (action) return 80;

    const normalizedTitle = normalizeHeader(title);
    if (!normalizedTitle) return 0;
    if (normalizedTitle === '#') return 48;

    return Math.round(
        clamp(
            normalizedTitle.length * HEADER_CHARACTER_WIDTH + HEADER_HORIZONTAL_SPACE,
            MINIMUM_HEADER_WIDTH,
            MAXIMUM_HEADER_WIDTH
        )
    );
}
