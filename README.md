# scripts Repository Guide

Welcome to the `scripts` repository. This guide will help you set up and run the provided scripts.

## **Table of Contents**

1. [Cloning the Repository](#cloning-the-repository)
2. [Prerequisites](#Prerequisites)
3. [Running the `setup.sh` Script](#running-the-setupsh-script)
3. [Running the `update.sh` Script](#running-the-updatesh-script)
3. [Running the `update-all.sh` Script](#running-the-update-allsh-script)

## **Cloning the Repository**

Clone the repository using the following command:

```bash
git clone https://github.com/programming101dev/scripts.git
```

Navigate to the cloned directory:

```bash
cd scripts
```

Ensure the scripts are executable:

```bash
chmod +x *.sh
```

## **Prerequisites**

To ensure you have all of the required tools installed, run:
```bash
./check-env.sh
```

If you are missing tools follow these [instructions](https://docs.google.com/document/d/1ZPqlPD1mie5iwJ2XAcNGz7WeA86dTLerFXs9sAuwCco/edit?usp=drive_link).

To determine which compilers you have installed on your system, run:
```bash
./check-compilers.sh
```

## **Running the setup.sh Script**

To setup the system the first time, run:

```bash
./setup.sh -c <c compiler> -x <c++ compiler>
```

To the see the list of possible compilers:
```bash
cat supported_c_compilers
cat supported_cxx_compilers
```

## **Running the update.sh Script**

After the system has been setup you will want to periodically update from github and rebuild:

```bash
./update.sh -c <c compiler> -x <c++ compiler>
```

## **Running the update-all.sh Script**

If you want to verify that the compiles with all of the compilers, run:

```bash
./update-all.sh
```
