import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * cn() is the small helper shadcn-ui uses everywhere:
 * merges Tailwind classes safely (tailwind-merge) and builds from conditional strings (clsx).
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

