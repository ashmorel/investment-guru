import "@testing-library/jest-dom/vitest";
import { expect } from "vitest";
// vitest-axe's root "matchers" subpath re-exports its types as `export type *`,
// which makes tsc treat this as type-only even though it is a real value at
// runtime. Import from the underlying dist file, which exports it as a value.
import { toHaveNoViolations } from "vitest-axe/dist/matchers";

expect.extend({ toHaveNoViolations });
