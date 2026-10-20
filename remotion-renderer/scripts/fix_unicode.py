content = open('train.py', 'r', encoding='utf-8').read()
# Replace unicode arrows and emojis with ASCII
content = content.replace('\u2192', '->')  # right arrow
content = content.replace('\u2714', '[OK]')  # heavy check mark
content = content.replace('\u274c', '[FAIL]')  # cross
content = content.replace('\u2705', '[OK]')  # white heavy check
open('train.py', 'w', encoding='utf-8').write(content)
print('done')