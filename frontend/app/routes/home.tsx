import type { Route } from "./+types/home";
import { Welcome } from "../welcome/welcome";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "VibeAudit" },
    { name: "description", content: "Welcome to VibeAudit." },
  ];
}

export default function Home() {
  return <Welcome />;
}
