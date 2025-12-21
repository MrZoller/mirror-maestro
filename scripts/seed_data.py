#!/usr/bin/env python3
"""
Seed the database with sample data for screenshots and testing.
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from app.database import AsyncSessionLocal, init_db
from app.models import GitLabInstance, InstancePair, Mirror
from app.core.encryption import encryption


async def seed_database():
    """Populate database with sample data."""
    print("Initializing database...")
    await init_db()

    async with AsyncSessionLocal() as session:
        print("Creating sample GitLab instances...")

        # Create sample instances
        instances = [
            GitLabInstance(
                name="Production GitLab",
                url="https://gitlab.company.com",
                encrypted_token=encryption.encrypt("glpat-xxxxxxxxxxxxxxxxxxxx"),
                description="Main production GitLab instance"
            ),
            GitLabInstance(
                name="Backup GitLab",
                url="https://backup.gitlab.company.com",
                encrypted_token=encryption.encrypt("glpat-yyyyyyyyyyyyyyyyyyyy"),
                description="Backup GitLab instance for disaster recovery"
            ),
            GitLabInstance(
                name="Development GitLab",
                url="https://gitlab-dev.company.com",
                encrypted_token=encryption.encrypt("glpat-zzzzzzzzzzzzzzzzzzzz"),
                description="Development and testing environment"
            ),
            GitLabInstance(
                name="Partner GitLab",
                url="https://gitlab.partner.com",
                encrypted_token=encryption.encrypt("glpat-aaaaaaaaaaaaaaaaaaaa"),
                description="Partner organization GitLab instance"
            )
        ]

        for instance in instances:
            session.add(instance)

        await session.commit()
        print(f"Created {len(instances)} GitLab instances")

        # Refresh to get IDs
        for instance in instances:
            await session.refresh(instance)

        print("Creating sample instance pairs...")

        # Create sample pairs
        pairs = [
            InstancePair(
                name="Prod → Backup",
                source_instance_id=instances[0].id,
                target_instance_id=instances[1].id,
                mirror_direction="push",
                mirror_protected_branches=True,
                mirror_overwrite_diverged=False,
                mirror_trigger_builds=False,
                only_mirror_protected_branches=False,
                description="Mirror production repositories to backup instance"
            ),
            InstancePair(
                name="Prod → Partner",
                source_instance_id=instances[0].id,
                target_instance_id=instances[3].id,
                mirror_direction="pull",
                mirror_protected_branches=True,
                mirror_overwrite_diverged=True,
                mirror_trigger_builds=True,
                only_mirror_protected_branches=True,
                description="Share selected projects with partner organization"
            ),
            InstancePair(
                name="Dev → Prod",
                source_instance_id=instances[2].id,
                target_instance_id=instances[0].id,
                mirror_direction="push",
                mirror_protected_branches=True,
                mirror_overwrite_diverged=False,
                mirror_trigger_builds=True,
                only_mirror_protected_branches=False,
                description="Promote tested code from dev to production"
            )
        ]

        for pair in pairs:
            session.add(pair)

        await session.commit()
        print(f"Created {len(pairs)} instance pairs")

        # Refresh to get IDs
        for pair in pairs:
            await session.refresh(pair)

        print("Creating sample mirrors...")

        # Create sample mirrors for Prod → Backup
        prod_backup_mirrors = [
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=123,
                source_project_path="platform/api-gateway",
                target_project_id=456,
                target_project_path="platform/api-gateway",
                mirror_id=1001,
                last_successful_update=datetime.utcnow() - timedelta(hours=2),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=124,
                source_project_path="platform/user-service",
                target_project_id=457,
                target_project_path="platform/user-service",
                mirror_id=1002,
                last_successful_update=datetime.utcnow() - timedelta(hours=1),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=125,
                source_project_path="platform/payment-processor",
                target_project_id=458,
                target_project_path="platform/payment-processor",
                mirror_id=1003,
                last_successful_update=datetime.utcnow() - timedelta(minutes=30),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=126,
                source_project_path="frontend/web-dashboard",
                target_project_id=459,
                target_project_path="frontend/web-dashboard",
                mirror_id=1004,
                last_successful_update=datetime.utcnow() - timedelta(hours=3),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=127,
                source_project_path="infrastructure/terraform-configs",
                target_project_id=460,
                target_project_path="infrastructure/terraform-configs",
                mirror_id=1005,
                last_successful_update=datetime.utcnow() - timedelta(minutes=45),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=128,
                source_project_path="data/analytics-pipeline",
                target_project_id=461,
                target_project_path="data/analytics-pipeline",
                mirror_id=1006,
                last_successful_update=datetime.utcnow() - timedelta(hours=5),
                last_update_status="failed",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=129,
                source_project_path="mobile/ios-app",
                target_project_id=462,
                target_project_path="mobile/ios-app",
                mirror_id=1007,
                last_successful_update=datetime.utcnow() - timedelta(days=1),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[0].id,
                source_project_id=130,
                source_project_path="mobile/android-app",
                target_project_id=463,
                target_project_path="mobile/android-app",
                mirror_id=1008,
                last_successful_update=None,
                last_update_status="pending",
                enabled=False
            )
        ]

        # Create sample mirrors for Prod → Partner
        prod_partner_mirrors = [
            Mirror(
                instance_pair_id=pairs[1].id,
                source_project_id=131,
                source_project_path="shared/api-client-library",
                target_project_id=501,
                target_project_path="integration/api-client",
                mirror_id=2001,
                last_successful_update=datetime.utcnow() - timedelta(hours=6),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[1].id,
                source_project_id=132,
                source_project_path="shared/data-schemas",
                target_project_id=502,
                target_project_path="integration/schemas",
                mirror_id=2002,
                last_successful_update=datetime.utcnow() - timedelta(hours=12),
                last_update_status="finished",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[1].id,
                source_project_id=133,
                source_project_path="docs/api-documentation",
                target_project_id=503,
                target_project_path="integration/api-docs",
                mirror_id=2003,
                last_successful_update=datetime.utcnow() - timedelta(days=2),
                last_update_status="finished",
                enabled=True
            )
        ]

        # Create sample mirrors for Dev → Prod
        dev_prod_mirrors = [
            Mirror(
                instance_pair_id=pairs[2].id,
                source_project_id=201,
                source_project_path="experiments/new-feature-x",
                target_project_id=301,
                target_project_path="features/feature-x",
                mirror_id=3001,
                last_successful_update=datetime.utcnow() - timedelta(days=1),
                last_update_status="updating",
                enabled=True
            ),
            Mirror(
                instance_pair_id=pairs[2].id,
                source_project_id=202,
                source_project_path="testing/automation-suite",
                target_project_id=302,
                target_project_path="qa/automation",
                mirror_id=3002,
                last_successful_update=datetime.utcnow() - timedelta(hours=8),
                last_update_status="finished",
                enabled=True
            )
        ]

        all_mirrors = prod_backup_mirrors + prod_partner_mirrors + dev_prod_mirrors

        for mirror in all_mirrors:
            session.add(mirror)

        await session.commit()
        print(f"Created {len(all_mirrors)} mirrors")

        print("\n✅ Database seeded successfully!")
        print("\nSummary:")
        print(f"  - {len(instances)} GitLab instances")
        print(f"  - {len(pairs)} instance pairs")
        print(f"  - {len(all_mirrors)} mirrors")
        print("\nYou can now start the application and take screenshots.")


if __name__ == "__main__":
    asyncio.run(seed_database())
