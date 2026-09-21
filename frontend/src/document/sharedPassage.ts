interface WordToken {
  start: number;
  end: number;
  normalized: string;
}

export interface TextRange {
  start: number;
  end: number;
}

export interface SharedPassageRanges {
  left: TextRange[];
  right: TextRange[];
}

const MIN_SHARED_WORDS = 3;
const MIN_SHARED_CHARACTERS = 14;

function words(text: string): WordToken[] {
  return Array.from(text.matchAll(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu), (match) => ({
    start: match.index,
    end: match.index + match[0].length,
    normalized: match[0].toLocaleLowerCase(),
  }));
}

interface Candidate {
  leftStart: number;
  rightStart: number;
  length: number;
  characters: number;
}

/** Finds exact shared word runs without claiming semantic similarity is exact. */
export function sharedPassageRanges(leftText: string, rightText: string): SharedPassageRanges {
  const leftWords = words(leftText);
  const rightWords = words(rightText);
  const next = Array.from({ length: leftWords.length + 1 }, () => new Array<number>(rightWords.length + 1).fill(0));
  const candidates: Candidate[] = [];

  for (let left = leftWords.length - 1; left >= 0; left -= 1) {
    for (let right = rightWords.length - 1; right >= 0; right -= 1) {
      if (leftWords[left].normalized !== rightWords[right].normalized) continue;
      next[left][right] = next[left + 1][right + 1] + 1;
      const length = next[left][right];
      const startsRun = left === 0 || right === 0 || leftWords[left - 1].normalized !== rightWords[right - 1].normalized;
      if (!startsRun || length < MIN_SHARED_WORDS) continue;
      const characters = leftWords[left + length - 1].end - leftWords[left].start;
      if (characters >= MIN_SHARED_CHARACTERS) candidates.push({ leftStart: left, rightStart: right, length, characters });
    }
  }

  candidates.sort((a, b) => b.length - a.length || b.characters - a.characters || a.leftStart - b.leftStart);
  const usedLeft = new Set<number>();
  const usedRight = new Set<number>();
  const selected = candidates.filter((candidate) => {
    for (let offset = 0; offset < candidate.length; offset += 1) {
      if (usedLeft.has(candidate.leftStart + offset) || usedRight.has(candidate.rightStart + offset)) return false;
    }
    for (let offset = 0; offset < candidate.length; offset += 1) {
      usedLeft.add(candidate.leftStart + offset);
      usedRight.add(candidate.rightStart + offset);
    }
    return true;
  });

  const left = selected.map((candidate) => ({
    start: leftWords[candidate.leftStart].start,
    end: leftWords[candidate.leftStart + candidate.length - 1].end,
  })).sort((a, b) => a.start - b.start);
  const right = selected.map((candidate) => ({
    start: rightWords[candidate.rightStart].start,
    end: rightWords[candidate.rightStart + candidate.length - 1].end,
  })).sort((a, b) => a.start - b.start);

  return { left, right };
}

