// Example of the functional core: pure, no I/O, trivially testable. Replace during bootstrap.
const MAX_NAME_LENGTH = 40;

export function greeting(name: string | null | undefined): string {
  const cleaned = (name ?? "").trim().slice(0, MAX_NAME_LENGTH);
  return cleaned ? `Hello, ${cleaned}!` : "Hello, world!";
}
