export function isStoryNumberText(value: string): boolean {
  if (!/^\d+$/.test(value.trim())) return false;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= 9999;
}

export function normalizedSearchText(parts: Array<string | number | null | undefined>): string {
  return parts
    .filter((item): item is string | number => item !== null && item !== undefined)
    .join(' ')
    .toLocaleLowerCase();
}
