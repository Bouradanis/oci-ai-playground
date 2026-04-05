# Updating OCI Config in WSL

## When to do this
- You regenerated your API key in OCI console
- You got a new `.pem` private key file
- Your config settings changed (tenancy, region, fingerprint, etc.)

---

## Key Paths to Remember

| Location | Path |
|---|---|
| WSL OCI folder | `/home/abourantanis/.oci/` |
| Windows OCI folder | `/mnt/c/Users/abour/.oci/` |
| Private key file | `among_idiots.pem` |

---

## Steps

### 1. Copy updated files from Windows to WSL

```bash
cp /mnt/c/Users/abour/.oci/config ~/.oci/config
cp /mnt/c/Users/abour/.oci/*.pem ~/.oci/
```

### 2. Fix the `key_file` path inside the config

```bash
nano ~/.oci/config
```

Change this:
```
key_file=C:\Users\abour\.oci\among_idiots.pem
```

To this:
```
key_file=/home/abourantanis/.oci/among_idiots.pem
```

Save: `Ctrl+O` → Enter → `Ctrl+X`

### 3. Fix file permissions

OCI SDK requires strict permissions on these files or it will refuse to use them.

```bash
chmod 600 ~/.oci/config
chmod 600 ~/.oci/*.pem
```

### 4. Verify it works

Run this in your notebook or Python terminal:

```python
import oci
config = oci.config.from_file()
print(config)  # should print tenancy, region, user etc. without errors
```

---

## Notes

- The `~` in bash is shorthand for `/home/abourantanis/`
- Your WSL home directory is accessible from Windows Explorer at: `\\wsl$\Ubuntu\home\abourantanis\`
- `chmod 600` means only you (the owner) can read/write the file — required by OCI SDK
- If you add a **new** key (not replace), make sure the filename in `key_file=` matches the actual `.pem` filename you copied