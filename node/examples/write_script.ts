/**
 * Write-like wrapper around `multi_edit` for game-tree scripts.
 *
 * See `writeScript` in `src/extended/writer.ts` for the full
 * documentation. Run as a script:
 *
 *     npx tsx examples/write_script.ts game.ServerScriptService.MyScript [studio_id] [--create]
 */

import { RobloxStudio } from "../src/index.js";
import { writeScript } from "../src/extended/index.js";

async function main(): Promise<void> {
  const rawArgs = process.argv.slice(2);
  const create = rawArgs.includes("--create");
  const positional = rawArgs.filter((a) => a !== "--create");

  if (positional.length < 1) {
    console.error(
      "Usage: npx tsx examples/write_script.ts <game.ScriptContainer.Name> [studio_id] [--create]",
    );
    process.exit(1);
  }

  const target = positional[0];
  const studioId = positional[1] ?? null;

  const studio = await RobloxStudio.connect({ studioId });
  try {
    const status = await writeScript(
      studio,
      target,
      "print('Hello from write_script!')",
      { createIfMissing: create },
    );
    if (status === "created") {
      console.log(`Created ${target}`);
    } else if (status === "wrote") {
      console.log(`Updated ${target}`);
    } else {
      console.log(`Unchanged: ${target} already has this content`);
    }
  } finally {
    await studio.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
