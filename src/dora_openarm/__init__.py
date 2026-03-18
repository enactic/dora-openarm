# Copyright 2026 Enactic, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Dora node to control OpenArm."""

import os

# Define the path to the README file relative to the package directory
readme_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "README.md")

# Read the content of the README file
try:
    with open(readme_path, encoding="utf-8") as f:
        __doc__ = f.read()
except FileNotFoundError:
    __doc__ = "README file not found."
