/**
 * Ambient declarations.
 *
 * Next injects a CSS module shim at build time via `next-env.d.ts`, but that
 * file is generated and `tsc --noEmit` runs before any build in this repo's
 * checks. Declaring it here keeps the typecheck honest without depending on a
 * build artifact existing.
 */

declare module '*.css'
