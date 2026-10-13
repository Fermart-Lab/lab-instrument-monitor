# Contributing

This is a private Fermart lab research repository. Use a short-lived branch and a pull request describing the problem, the change, and validation results.

Do not commit credentials, `.env`, experimental recordings, generated build files, or local calibration. Use synthetic fixtures for tests. Keep hardware-specific changes small and record the device, firmware, configuration, and observed behavior.

Run the checks in `.github/workflows/ci.yml` before requesting review. Automated checks do not validate physical equipment. Changes to motion, calibration, output control, or instrument commands require supervised lab validation before use.

Preserve third-party notices. Repository privacy does not grant permission to redistribute institutional code or data. Licensing of first-party code must be confirmed by the lab before public distribution.
