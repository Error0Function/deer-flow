const RAW_BASE = import.meta.env.BASE_URL || "/";

export const APP_BASE_PATH = normalizeBasePath(RAW_BASE);

export function withAppBasePath(path = "/") {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return APP_BASE_PATH === "/" ? normalizedPath : `${APP_BASE_PATH}${normalizedPath}`;
}

function normalizeBasePath(path: string) {
  if (!path || path === "/") {
    return "/";
  }

  const withLeadingSlash = path.startsWith("/") ? path : `/${path}`;
  const withoutTrailingSlash =
    withLeadingSlash.endsWith("/") && withLeadingSlash.length > 1
      ? withLeadingSlash.slice(0, -1)
      : withLeadingSlash;

  return withoutTrailingSlash || "/";
}
