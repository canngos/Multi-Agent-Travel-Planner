export type Selection =
  | { kind: "agent"; name: string }
  | { kind: "tool"; id: string }
  | { kind: "state"; key: string };
