/** Fit only after Markmap's SVG viewport has a real, connected layout box. */
export function fitMarkmapWhenSized(container, fit) {
  if (!container?.isConnected) return false;

  const { width, height } = container.getBoundingClientRect();
  if (width <= 0 || height <= 0) return false;

  fit();
  return true;
}
