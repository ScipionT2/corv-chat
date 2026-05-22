# How to Run the Creative Team

## Quick Invocation
When you need the DESI/LESI/REDI pipeline, use this pattern:

### For UI/CSS work:
```
1. Spawn DESI subagent with the design brief + reference image
   - DESI reads agents/DESI.md for personality
   - DESI produces CSS/HTML output

2. Spawn LESI subagent with DESI's output + reference image
   - LESI reads agents/LESI.md for personality
   - LESI produces structured review with fixes

3. Spawn REDI subagent with DESI's output + reference image
   - REDI reads agents/REDI.md for personality
   - REDI produces cinematic/emotional review

4. Spawn DESI again with LESI + REDI feedback
   - DESI revises the output
   - Repeat 2-3 until both reviewers give A+ grade
```

### For image generation:
```
1. DESI writes the image prompt (style, mood, composition)
2. Generate the image
3. LESI reviews the image (technical accuracy)
4. REDI reviews the image (cinematic feel)
5. Refine prompt based on feedback, regenerate
```

### For branding/concept work:
```
1. DESI creates the concept (colors, typography, layout)
2. LESI validates consistency with existing Nova branding
3. REDI validates emotional impact
4. Iterate
```

## Important Notes
- Always pass the reference image to ALL three agents
- DESI is the only one who creates — LESI and REDI only review
- Don't skip the review loop — it's what makes the output premium
- Maximum 3 iterations usually sufficient
- If both reviewers give A+ on first pass, ship it
