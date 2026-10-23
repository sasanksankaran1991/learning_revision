import { FormEvent } from "react";

export async function submit(event: FormEvent, action: () => Promise<void>, setError: (value: string) => void) {
  event.preventDefault();
  setError("");
  try {
    await action();
  } catch (err) {
    setError(err instanceof Error ? err.message : "Something went wrong");
  }
}
