// Task dialogs pause optional guidance without changing its saved progress.
import { useSyncExternalStore } from "react";

let taskModals = 0;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const taskModalActive = () => taskModals > 0;
const serverSnapshot = () => false;

export function registerTaskModal() {
  taskModals += 1;
  if (taskModals === 1) listeners.forEach((listener) => listener());
  let owned = true;
  return () => {
    if (!owned) return;
    owned = false;
    taskModals -= 1;
    if (taskModals === 0) listeners.forEach((listener) => listener());
  };
}

export function useTaskModalActive() {
  return useSyncExternalStore(subscribe, taskModalActive, serverSnapshot);
}
