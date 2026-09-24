import {
  Archive,
  Database,
  Globe,
  Inbox,
  Leaf,
  Logs,
  Network,
  Rabbit,
  Router,
  Server,
  ShipWheel,
  Zap,
} from "lucide-react";
import { describe, expect, it } from "vitest";

import { COMPONENT_TYPE_META, componentIcon } from "@/features/architecture/constants";
import type { ComponentType } from "@/types/component";

function icon(type: ComponentType, technology: string, name = "Component") {
  return componentIcon({ type, technology, name });
}

describe("component icons (spec §99)", () => {
  it.each([
    ["database", "PostgreSQL 16", Database],
    ["database", "MySQL", Database],
    ["database", "Amazon Aurora", Database],
    ["database", "MongoDB Atlas", Leaf],
    ["cache", "Redis 7", Zap],
    ["cache", "Memcached", Zap],
    ["queue", "Apache Kafka", Logs],
    ["queue", "RabbitMQ", Rabbit],
    ["queue", "Amazon SQS", Inbox],
    ["storage", "S3", Archive],
    ["storage", "Object storage", Archive],
    ["service", "Kubernetes (EKS)", ShipWheel],
    ["load_balancer", "NGINX", Network],
    ["cdn", "CloudFront", Globe],
    ["gateway", "Kong", Router],
  ] as const)("%s / %s", (type, technology, expected) => {
    expect(icon(type, technology)).toBe(expected);
  });

  it("matches the name when the technology is unknown", () => {
    expect(icon("database", "Managed", "PostgreSQL")).toBe(Database);
  });

  it("does not match fragments of other words", () => {
    // "s3" and "sqs" are matched as whole words only.
    expect(icon("service", "Go", "Settings3 service")).toBe(Server);
  });

  it("falls back to the category icon", () => {
    for (const type of Object.keys(COMPONENT_TYPE_META) as ComponentType[]) {
      expect(icon(type, "Something bespoke", "Thing")).toBe(COMPONENT_TYPE_META[type].Icon);
    }
  });
});
