"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS: { href: string; label: string }[] = [
  { href: "/", label: "Make a Call" },
  { href: "/monitor", label: "Monitor" },
  { href: "/calls", label: "Call History" },
];

export default function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="border-b bg-white">
      <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
        <Link href="/" className="font-bold text-gray-900">
          Voice Agent
        </Link>
        <div className="flex items-center gap-1">
          {LINKS.map(({ href, label }) => {
            // Exact match for home, prefix match for the rest.
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  active
                    ? "bg-gray-900 text-white"
                    : "text-gray-600 hover:bg-gray-100"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
