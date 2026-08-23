using System;
using System.Security.Cryptography;

if (SHA256.HashData("rpmtest"u8).Length != 32)
{
    throw new InvalidOperationException("SHA-256 failed");
}
Console.WriteLine($"rpmtest .NET works: {19 + 23}");
