with open('web/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# The problematic string is:
# ' style="' + (m.type === 'video' && m.has_thumb ? 'position:relative' : '') + '">' + thumb +
# When has_thumb=true:  style="'position:relative">  <- > is inside attribute value!
# Fix: change '">' to '" + "'>' so it becomes: style="'position:relative" + ">"

old_str = "? 'position:relative' : '') + '"
new_str = "? 'position:relative' : '') + '"

idx = content.find(old_str)
print('Found at:', idx)
print('Surrounding:', repr(content[idx-10:idx+60]))

# The exact replacement: change "> to " + ">
old_sub = old_str + '">'
new_sub = new_str + '"\' + "\'>'

print('Old_sub found:', old_sub in content)
new_content = content.replace(old_sub, new_sub, 1)
print('New_sub found:', new_sub in new_content)

with open('web/index.html', 'w', encoding='utf-8') as f:
    f.write(new_content)
print('Done')
