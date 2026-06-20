export type ThemeMode = "light" | "dark";

export const themeStorageKey = "agromech.theme";

export function normalizeTheme(value: string | null | undefined): ThemeMode | null {
  return value === "light" || value === "dark" ? value : null;
}

export function resolveInitialTheme(storedTheme: string | null | undefined, prefersDark: boolean): ThemeMode {
  return normalizeTheme(storedTheme) ?? (prefersDark ? "dark" : "light");
}

export function nextTheme(currentTheme: ThemeMode): ThemeMode {
  return currentTheme === "dark" ? "light" : "dark";
}
